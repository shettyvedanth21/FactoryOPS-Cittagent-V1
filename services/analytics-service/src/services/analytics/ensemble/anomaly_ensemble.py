"""3-model anomaly ensemble orchestrator."""

from typing import Any, Dict, List

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from src.services.analytics.anomaly_detection import AnomalyDetectionPipeline
from src.services.analytics.ensemble.voting_engine import VotingEngine
from src.services.analytics.explainer.reasoning_engine import ReasoningEngine
from src.services.analytics.features.feature_engineer import FeatureEngineer
from src.services.analytics.features.sequence_builder import SequenceBuilder
from src.services.analytics.models.cusum_detector import CUSUMDetector
from src.services.analytics.models.lstm_autoencoder import LSTMAnomalyAutoencoder

SEQUENCE_LENGTH = 30


class AnomalyEnsemble:
    """Runs IF + LSTM autoencoder + CUSUM and combines via voting."""

    @staticmethod
    def _parse_utc_timestamps(values: Any) -> pd.DatetimeIndex:
        """Parse mixed timestamp values deterministically without inference warnings."""
        if isinstance(values, pd.DatetimeIndex):
            return values.tz_convert("UTC") if values.tz is not None else values.tz_localize("UTC")
        series = pd.Series(values)
        if pd.api.types.is_datetime64_any_dtype(series):
            parsed = pd.to_datetime(series, utc=True, errors="coerce")
        else:
            parsed = pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")
        return pd.DatetimeIndex(parsed)

    def run(self, df: pd.DataFrame, parameters: Dict[str, Any] | None = None) -> Dict[str, Any]:
        params = parameters or {}
        fe = FeatureEngineer()
        numeric_cols = fe.get_numeric_cols(df)

        if_pipeline = AnomalyDetectionPipeline()
        train_df, _ = if_pipeline.prepare_data(df, params)
        if_model = if_pipeline.train(train_df, "isolation_forest", params)
        if_result = if_pipeline.predict(df, if_model, params)

        n_if = len(if_result.get("is_anomaly", []))
        if n_if == 0:
            return {
                "is_anomaly": [],
                "anomaly_score": [],
                "anomaly_details": [],
                "point_timestamps": [],
                "total_anomalies": 0,
                "anomaly_percentage": 0.0,
                "ensemble": {},
                "reasoning": {},
                "data_quality_flags": self._flags(df, False),
            }

        lstm_result = {
            "is_anomaly": np.zeros(n_if, dtype=bool),
            "anomaly_score": np.zeros(n_if, dtype=float),
            "is_trained": False,
        }

        if numeric_cols:
            base = self._to_timestamp_index(df)
            scaled = StandardScaler().fit_transform(base[numeric_cols].fillna(0))
            scaled_df = pd.DataFrame(scaled, index=base.index, columns=numeric_cols)

            seq_b = SequenceBuilder()
            sequences, ts = seq_b.build_sequences(scaled_df, SEQUENCE_LENGTH, numeric_cols)
            lstm_ae = LSTMAnomalyAutoencoder()
            trained = lstm_ae.train(sequences)
            lstm_raw = lstm_ae.predict(sequences)

            if isinstance(if_result.get("point_timestamps"), list):
                target_ts = self._parse_utc_timestamps(if_result["point_timestamps"])
            else:
                target_ts = self._parse_utc_timestamps(base.index)

            ts_index_map = {pd.Timestamp(t).isoformat(): i for i, t in enumerate(ts)}
            is_anomaly = np.zeros(len(target_ts), dtype=bool)
            anomaly_score = np.zeros(len(target_ts), dtype=float)
            for i, t in enumerate(target_ts):
                if pd.isna(t):
                    continue
                idx = ts_index_map.get(pd.Timestamp(t).isoformat())
                if idx is None:
                    continue
                is_anomaly[i] = bool(lstm_raw["is_anomaly"][idx])
                anomaly_score[i] = float(lstm_raw["anomaly_score"][idx])

            lstm_result = {
                "is_anomaly": is_anomaly,
                "anomaly_score": anomaly_score,
                "is_trained": trained,
            }

        target_ts = if_result.get("point_timestamps", [])
        base_if_df = self._aligned_numeric_frame(df, numeric_cols, target_ts)
        cusum_result = CUSUMDetector().detect(
            base_if_df,
            [c for c in numeric_cols if c in base_if_df.columns],
        )

        vote = VotingEngine().vote_anomaly(if_result, lstm_result, cusum_result)

        affected = self._affected_parameters(if_result)
        reasoning = ReasoningEngine().generate_anomaly_reasoning(
            {
                "confidence": self._summary_confidence(vote.get("confidence", [])),
            },
            affected,
        )

        vote_count = vote.get("vote_count", [])
        confidence = vote.get("confidence", [])
        combined_score = vote.get("combined_score", [])
        models_voted = vote.get("models_voted", [])

        anomaly_details = []
        if_details = if_result.get("anomaly_details", []) or []
        for i, detail in enumerate(if_details):
            vc = int(vote_count[i]) if i < len(vote_count) else 0
            conf = confidence[i] if i < len(confidence) else "NORMAL"
            mv = models_voted[i] if i < len(models_voted) else []
            enriched = dict(detail)
            enriched["vote_count"] = vc
            enriched["ensemble_confidence"] = conf
            enriched["models_voted"] = mv
            anomaly_details.append(enriched)

        summary_vc = int(max(vote_count)) if vote_count else 0
        summary_conf = self._summary_confidence(confidence)

        return {
            "anomaly_score": combined_score,
            "is_anomaly": vote.get("is_anomaly", []),
            "point_timestamps": if_result.get("point_timestamps", []),
            "anomaly_details": anomaly_details,
            "columns_used": if_result.get("columns_used", []),
            "total_anomalies": int(sum(1 for x in vote.get("is_anomaly", []) if x)),
            "anomaly_percentage": float((sum(1 for x in vote.get("is_anomaly", []) if x) / max(1, len(vote.get("is_anomaly", [])))) * 100),
            "data_completeness_pct": float(if_result.get("data_completeness_pct", 100.0)),
            "single_parameter_mode": bool(if_result.get("single_parameter_mode", False)),
            "days_available": float(if_result.get("days_available", 0.0)),
            "insufficient_data": bool(if_result.get("insufficient_data", False)),
            "confidence": if_result.get("confidence", {}),
            "ensemble": {
                "vote_count": summary_vc,
                "confidence": summary_conf,
                "models_voted": models_voted,
                "per_model": vote.get("per_model", {}),
                "timeline": {
                    "vote_count": vote_count,
                    "confidence": confidence,
                    "models_voted": models_voted,
                },
            },
            "reasoning": reasoning,
            "data_quality_flags": self._flags(df, bool(lstm_result.get("is_trained", False))),
            "cusum_drift_params": cusum_result.get("drift_params", []),
        }

    @staticmethod
    def _to_timestamp_index(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "timestamp" not in out.columns and "_time" in out.columns:
            out = out.rename(columns={"_time": "timestamp"})
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
        out = out.dropna(subset=["timestamp"]).sort_values("timestamp")
        return out.set_index("timestamp")

    @staticmethod
    def _aligned_numeric_frame(
        df: pd.DataFrame,
        numeric_cols: List[str],
        target_ts: List[str],
    ) -> pd.DataFrame:
        if not target_ts:
            return pd.DataFrame(index=pd.DatetimeIndex([]))
        base = df.copy()
        if "timestamp" not in base.columns and "_time" in base.columns:
            base = base.rename(columns={"_time": "timestamp"})
        base["timestamp"] = pd.to_datetime(base["timestamp"], utc=True, errors="coerce")
        base = base.dropna(subset=["timestamp"]).sort_values("timestamp")
        if not numeric_cols:
            return pd.DataFrame(index=AnomalyEnsemble._parse_utc_timestamps(target_ts))
        clean = base[["timestamp"] + [c for c in numeric_cols if c in base.columns]].copy()
        clean = clean.set_index("timestamp").resample("1min").mean()
        clean = clean.ffill(limit=15).bfill(limit=15).fillna(0)
        idx = AnomalyEnsemble._parse_utc_timestamps(target_ts)
        return clean.reindex(idx, method="nearest").fillna(0)

    @staticmethod
    def _summary_confidence(confidence: List[str]) -> str:
        if not confidence:
            return "NORMAL"
        if "HIGH" in confidence:
            return "HIGH"
        if "MEDIUM" in confidence:
            return "MEDIUM"
        if "LOW" in confidence:
            return "LOW"
        return "NORMAL"

    @staticmethod
    def _affected_parameters(if_result: Dict[str, Any]) -> List[str]:
        details = if_result.get("anomaly_details", []) or []
        counts: Dict[str, int] = {}
        for d in details:
            for p in d.get("parameters", []):
                counts[p] = counts.get(p, 0) + 1
        return [p for p, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5]]

    @staticmethod
    def _flags(df: pd.DataFrame, lstm_trained: bool) -> List[Dict[str, str]]:
        flags: List[Dict[str, str]] = []
        if len(df) <= 1:
            days = 0.0
        else:
            idx_df = df.copy()
            if "timestamp" not in idx_df.columns and "_time" in idx_df.columns:
                idx_df = idx_df.rename(columns={"_time": "timestamp"})
            idx_df["timestamp"] = pd.to_datetime(idx_df["timestamp"], utc=True, errors="coerce")
            idx_df = idx_df.dropna(subset=["timestamp"]).sort_values("timestamp")
            days = (idx_df["timestamp"].iloc[-1] - idx_df["timestamp"].iloc[0]).total_seconds() / 86400 if len(idx_df) > 1 else 0.0

        if days < (1 / 24):
            flags.append(
                {
                    "type": "data_confidence",
                    "confidence_level": "Very Low",
                    "color": "red",
                    "message": "Demo only — less than 1 hour of data.",
                    "severity": "warning",
                }
            )
        elif days < 1:
            flags.append(
                {
                    "type": "data_confidence",
                    "confidence_level": "Low",
                    "color": "orange",
                    "message": f"Only {int(days * 24)}h of data — directional only.",
                    "severity": "info",
                }
            )

        if not lstm_trained:
            flags.append(
                {
                    "type": "lstm_not_trained",
                    "message": "Temporal model skipped — need 50+ sequences.",
                    "severity": "info",
                }
            )
        return flags
