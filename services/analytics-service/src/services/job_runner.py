

# """Job runner abstraction for executing analytics jobs."""

# import time
# from datetime import datetime
# from typing import Any, Dict

# import pandas as pd
# import structlog

# from src.models.schemas import AnalyticsRequest, AnalyticsType, JobStatus
# from src.services.analytics.anomaly_detection import AnomalyDetectionPipeline
# from src.services.analytics.failure_prediction import FailurePredictionPipeline
# from src.services.analytics.forecasting import ForecastingPipeline
# from src.services.dataset_service import DatasetService
# from src.services.result_repository import ResultRepository
# from src.utils.exceptions import AnalyticsError

# logger = structlog.get_logger()


# class JobRunner:
#     """Runner for executing analytics jobs."""

#     def __init__(
#         self,
#         dataset_service: DatasetService,
#         result_repository: ResultRepository,
#     ):
#         self._dataset_service = dataset_service
#         self._result_repo = result_repository
#         self._logger = logger.bind(service="JobRunner")

#         self._pipelines = {
#             AnalyticsType.ANOMALY: AnomalyDetectionPipeline(),
#             AnalyticsType.PREDICTION: FailurePredictionPipeline(),
#             AnalyticsType.FORECAST: ForecastingPipeline(),
#         }

#     async def run_job(self, job_id: str, request: AnalyticsRequest) -> None:
#         start_clock = time.time()

#         self._logger.info(
#             "job_started",
#             job_id=job_id,
#             analysis_type=request.analysis_type.value,
#             model_name=request.model_name,
#         )

#         try:
#             await self._result_repo.update_job_status(
#                 job_id=job_id,
#                 status=JobStatus.RUNNING,
#                 started_at=datetime.utcnow(),
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 10.0, "Loading dataset"
#             )

#             # -------------------------------------------------------
#             # Dataset loading
#             # -------------------------------------------------------
#             df = await self._dataset_service.load_dataset(
#                 device_id=request.device_id,
#                 start_time=request.start_time,
#                 end_time=request.end_time,
#                 s3_key=getattr(request, "dataset_key", None),
#             )

#             pipeline = self._pipelines.get(request.analysis_type)
#             if not pipeline:
#                 raise AnalyticsError(
#                     f"Unknown analysis type: {request.analysis_type}"
#                 )

#             await self._result_repo.update_job_progress(
#                 job_id, 30.0, "Preparing features"
#             )

#             train_df, test_df = pipeline.prepare_data(
#                 df, request.parameters
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 50.0, "Training model"
#             )

#             model = pipeline.train(
#                 train_df, request.model_name, request.parameters
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 75.0, "Running inference"
#             )

#             # -------------------------------------------------------
#             # PERMANENT FIX
#             # Inference must run on FULL dataframe
#             # -------------------------------------------------------
#             results = pipeline.predict(
#                 df, model, request.parameters
#             )

#             await self._result_repo.update_job_progress(
#                 job_id, 90.0, "Calculating metrics"
#             )

#             # evaluation is still done only on test split
#             metrics = pipeline.evaluate(
#                 test_df, results, request.parameters
#             )

#             # ---------------------------------------------------------
#             # Attach timestamp aligned points for anomaly jobs
#             # (must use FULL dataframe)
#             # ---------------------------------------------------------
#             if request.analysis_type == AnalyticsType.ANOMALY:
#                 self._attach_anomaly_points(results, df)

#             execution_time = int(time.time() - start_clock)

#             await self._result_repo.save_results(
#                 job_id=job_id,
#                 results=results,
#                 accuracy_metrics=metrics,
#                 execution_time_seconds=execution_time,
#             )

#             await self._result_repo.update_job_status(
#                 job_id=job_id,
#                 status=JobStatus.COMPLETED,
#                 completed_at=datetime.utcnow(),
#                 progress=100.0,
#                 message="Analysis completed successfully",
#             )

#             self._logger.info(
#                 "job_completed",
#                 job_id=job_id,
#                 execution_time_seconds=execution_time,
#             )

#         except Exception as e:
#             self._logger.error(
#                 "job_failed",
#                 job_id=job_id,
#                 error=str(e),
#                 exc_info=True,
#             )

#             await self._result_repo.update_job_status(
#                 job_id=job_id,
#                 status=JobStatus.FAILED,
#                 completed_at=datetime.utcnow(),
#                 message="Job failed",
#                 error_message=str(e),
#             )

#             raise AnalyticsError(f"Job execution failed: {e}") from e

#     def _attach_anomaly_points(
#         self,
#         results: Dict[str, Any],
#         df: pd.DataFrame,
#     ) -> None:
#         """
#         Attach timestamp-aligned anomaly points to results.

#         Produces:
#         results["points"] = [
#             {
#                 "timestamp": ...,
#                 "anomaly_score": ...,
#                 "is_anomaly": ...
#             }
#         ]
#         """

#         # ---------------------------------------------------------
#         # Robust timestamp column detection
#         # ---------------------------------------------------------
#         if "timestamp" in df.columns:
#             ts_col = "timestamp"
#         elif "_time" in df.columns:
#             ts_col = "_time"
#         else:
#             raise AnalyticsError(
#                 "No timestamp column found in dataset (expected 'timestamp' or '_time')"
#             )

#         anomaly_scores = results.get("anomaly_score")
#         is_anomaly = results.get("is_anomaly")

#         if anomaly_scores is None or is_anomaly is None:
#             raise AnalyticsError(
#                 "Anomaly results missing 'anomaly_score' or 'is_anomaly'"
#             )

#         if len(df) != len(anomaly_scores):
#             raise AnalyticsError(
#                 "Mismatch between dataframe length and anomaly result length"
#             )

#         timestamps = pd.to_datetime(
#             df[ts_col],
#             utc=True,
#             errors="coerce",
#         )

#         if timestamps.isna().any():
#             raise AnalyticsError(
#                 "Invalid timestamp values found in dataset"
#             )

#         points = []

#         for ts, score, flag in zip(
#             timestamps,
#             anomaly_scores,
#             is_anomaly,
#         ):
#             points.append(
#                 {
#                     "timestamp": ts.isoformat(),
#                     "anomaly_score": float(score),
#                     "is_anomaly": bool(flag),
#                 }
#             )

#         results["points"] = points
























"""Job runner abstraction for executing analytics jobs."""

import math
import time
from datetime import datetime, timezone
from typing import Any, Dict

import pandas as pd
import structlog
from sqlalchemy import select

from src.config.settings import get_settings
from src.infrastructure.database import async_session_maker
from src.infrastructure.s3_client import S3Client
from src.models.database import AccuracyEvaluation
from src.models.schemas import AnalyticsRequest, AnalyticsType, JobStatus
from src.services.analytics.ensemble.anomaly_ensemble import AnomalyEnsemble
from src.services.analytics.ensemble.failure_ensemble import FailureEnsemble
from src.services.analytics.forecasting import ForecastingPipeline
from src.services.dataset_service import DatasetService
from src.services.readiness_orchestrator import dataset_window_from_key, ensure_device_ready
from src.services.result_formatter import ResultFormatter
from src.services.result_repository import ResultRepository
from src.utils.exceptions import AnalyticsError

logger = structlog.get_logger()


# ----------------------------------------------------------------------
# Permanent JSON safety boundary
# ----------------------------------------------------------------------
def _json_safe(obj: Any):
    # Handle numpy-like arrays without importing numpy in hot path.
    if hasattr(obj, "tolist") and not isinstance(obj, (str, bytes, bytearray)):
        try:
            return _json_safe(obj.tolist())
        except Exception:
            pass

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]

    return obj


class JobRunner:
    """Runner for executing analytics jobs."""

    def __init__(
        self,
        dataset_service: DatasetService,
        result_repository: ResultRepository,
    ):
        self._dataset_service = dataset_service
        self._result_repo = result_repository
        self._logger = logger.bind(service="JobRunner")

        self._pipelines = {
            AnalyticsType.FORECAST: ForecastingPipeline(),
        }

    async def run_job(self, job_id: str, request: AnalyticsRequest) -> None:
        start_clock = time.time()
        params = request.parameters or {}
        settings = get_settings()
        resolved_request = request

        self._logger.info(
            "job_started",
            job_id=job_id,
            analysis_type=request.analysis_type.value,
            model_name=request.model_name,
        )

        try:
            await self._result_repo.update_job_status(
                job_id=job_id,
                status=JobStatus.RUNNING,
                started_at=datetime.utcnow(),
            )

            await self._result_repo.update_job_progress(
                job_id, 10.0, "Loading dataset"
            )

            readiness_enabled = (
                settings.app_env.lower() != "test"
                and (settings.ml_require_exact_dataset_range or settings.ml_data_readiness_gate_enabled)
            )

            if (
                not request.dataset_key
                and request.start_time
                and request.end_time
                and request.analysis_type in {AnalyticsType.ANOMALY, AnalyticsType.PREDICTION}
                and readiness_enabled
            ):
                await self._result_repo.update_job_progress(
                    job_id, 15.0, "Preparing exact-range dataset"
                )
                s3_client = S3Client()
                ready_device_id, dataset_key, readiness_meta = await ensure_device_ready(
                    s3_client=s3_client,
                    dataset_service=self._dataset_service,
                    device_id=request.device_id,
                    start_time=request.start_time,
                    end_time=request.end_time,
                )
                if not dataset_key:
                    reason = str((readiness_meta or {}).get("reason") or "dataset_not_ready")
                    hard_reasons = {"device_not_found", "no_telemetry_in_range"}
                    if reason in hard_reasons:
                        code = "DATASET_NOT_READY_TIMEOUT" if reason == "export_timeout" else "DATASET_NOT_READY"
                        raise AnalyticsError(
                            f"{code}: readiness failed for device={ready_device_id}, "
                            f"reason={reason}, export_attempted={bool((readiness_meta or {}).get('export_attempted', False))}, "
                            f"wait_seconds={float((readiness_meta or {}).get('wait_seconds', 0.0))}"
                        )
                    # Permanent hardening:
                    # when export/S3 path is unavailable, continue with exact-range direct data-service fetch.
                    resolved_request = request
                    await self._result_repo.update_job_progress(
                        job_id, 20.0, "Loading exact-range telemetry directly"
                    )
                else:
                    resolved_request = request.model_copy(update={"dataset_key": dataset_key})
                    await self._result_repo.update_job_progress(
                        job_id, 20.0, "Running analysis"
                    )

            # -------------------------------------------------------
            # Dataset loading
            # -------------------------------------------------------
            df = await self._dataset_service.load_dataset(
                device_id=resolved_request.device_id,
                start_time=resolved_request.start_time,
                end_time=resolved_request.end_time,
                s3_key=getattr(resolved_request, "dataset_key", None),
            )

            await self._result_repo.update_job_progress(
                job_id, 30.0, "Preparing features"
            )
            if request.analysis_type == AnalyticsType.ANOMALY:
                await self._result_repo.update_job_progress(
                    job_id, 50.0, "Training ensemble models"
                )
                ensemble = AnomalyEnsemble()
                await self._result_repo.update_job_progress(
                    job_id, 75.0, "Running ensemble inference"
                )
                results = ensemble.run(df, params)
                await self._result_repo.update_job_progress(
                    job_id, 90.0, "Calculating metrics"
                )
                total = len(results.get("is_anomaly", []))
                detected = int(sum(1 for x in results.get("is_anomaly", []) if x))
                scores = pd.Series(results.get("anomaly_score", []), dtype=float)
                metrics = {
                    "total_points": float(total),
                    "anomalies_detected": float(detected),
                    "anomaly_rate_pct": float((detected / total * 100) if total else 0.0),
                    "mean_anomaly_score": float(scores.mean()) if len(scores) else 0.0,
                    "max_anomaly_score": float(scores.max()) if len(scores) else 0.0,
                }
            elif request.analysis_type == AnalyticsType.PREDICTION:
                await self._result_repo.update_job_progress(
                    job_id, 50.0, "Training ensemble models"
                )
                preloaded_artifacts: Dict[str, Any] = {}
                try:
                    xgb_art = await self._result_repo.get_model_artifact(
                        device_id=request.device_id,
                        analysis_type=request.analysis_type.value,
                        model_key="xgboost",
                    )
                    if xgb_art:
                        preloaded_artifacts["xgboost"] = xgb_art
                except Exception:
                    preloaded_artifacts = {}
                ensemble = FailureEnsemble()
                await self._result_repo.update_job_progress(
                    job_id, 75.0, "Running ensemble inference"
                )
                run_params = dict(params)
                run_params["__artifacts"] = preloaded_artifacts
                results = ensemble.run(df, run_params)
                artifact_updates = results.pop("artifact_updates", {}) if isinstance(results, dict) else {}
                try:
                    xgb_update = (artifact_updates or {}).get("xgboost", {})
                    xgb_payload = xgb_update.get("artifact_payload")
                    xgb_schema = xgb_update.get("feature_schema_hash")
                    if xgb_payload and xgb_schema:
                        await self._result_repo.upsert_model_artifact(
                            device_id=request.device_id,
                            analysis_type=request.analysis_type.value,
                            model_key="xgboost",
                            feature_schema_hash=str(xgb_schema),
                            artifact_payload=xgb_payload,
                            metrics=xgb_update.get("metrics", {}),
                        )
                except Exception:
                    pass
                await self._result_repo.update_job_progress(
                    job_id, 90.0, "Calculating metrics"
                )
                metrics = {
                    "failure_probability_pct": float(results.get("failure_probability_pct", 0.0)),
                    "model_confidence": str(results.get("model_confidence", "Low")),
                }
                cert_flag = await self._latest_accuracy_flag(request.device_id)
                if cert_flag:
                    results.setdefault("data_quality_flags", []).append(cert_flag)
            else:
                pipeline = self._pipelines.get(request.analysis_type)
                if not pipeline:
                    raise AnalyticsError(
                        f"Unknown analysis type: {request.analysis_type}"
                    )

                train_df, test_df = pipeline.prepare_data(
                    df, params
                )

                await self._result_repo.update_job_progress(
                    job_id, 50.0, "Training model"
                )

                model = pipeline.train(
                    train_df, resolved_request.model_name, params
                )

                await self._result_repo.update_job_progress(
                    job_id, 75.0, "Running inference"
                )

                results = pipeline.predict(
                    df, model, params
                )

                await self._result_repo.update_job_progress(
                    job_id, 90.0, "Calculating metrics"
                )

                metrics = pipeline.evaluate(
                    test_df, results, params
                )

            # ---------------------------------------------------------
            # Attach timestamp aligned points
            # ---------------------------------------------------------
            if request.analysis_type == AnalyticsType.ANOMALY:
                self._attach_anomaly_points(results, df)

            if request.analysis_type == AnalyticsType.PREDICTION:
                self._attach_failure_points(results, df)

            if settings.ml_formatted_results_enabled:
                requested_start = (
                    (
                        resolved_request.start_time.astimezone(timezone.utc)
                        if resolved_request.start_time.tzinfo
                        else resolved_request.start_time.replace(tzinfo=timezone.utc)
                    ).isoformat()
                    if resolved_request.start_time
                    else None
                )
                requested_end = (
                    (
                        resolved_request.end_time.astimezone(timezone.utc)
                        if resolved_request.end_time.tzinfo
                        else resolved_request.end_time.replace(tzinfo=timezone.utc)
                    ).isoformat()
                    if resolved_request.end_time
                    else None
                )
                dataset_window = dataset_window_from_key(getattr(resolved_request, "dataset_key", None))
                formatter = ResultFormatter()
                if request.analysis_type == AnalyticsType.ANOMALY:
                    results["formatted"] = formatter.format_anomaly_results(
                        device_id=request.device_id,
                        job_id=job_id,
                        anomaly_details=results.get("anomaly_details", []),
                        total_points=len(results.get("is_anomaly", [])),
                        sensitivity=params.get("sensitivity", "medium"),
                        lookback_days=int(params.get("lookback_days", 7)),
                        metadata={
                            "data_completeness_pct": results.get("data_completeness_pct", 100.0),
                            "fallback_mode": results.get("fallback_mode", False),
                            "days_available": results.get("days_available", params.get("lookback_days", 7)),
                            "data_points_analyzed": len(results.get("is_anomaly", [])),
                            "requested_range": {"start_time": requested_start, "end_time": requested_end},
                            "dataset_range": dataset_window,
                        },
                        ensemble=results.get("ensemble"),
                        reasoning=results.get("reasoning"),
                        data_quality_flags=results.get("data_quality_flags"),
                    )
                elif request.analysis_type == AnalyticsType.PREDICTION:
                    results["formatted"] = formatter.format_failure_prediction_results(
                        device_id=request.device_id,
                        job_id=job_id,
                        failure_probability_pct=results.get("failure_probability_pct", 0.0),
                        risk_breakdown=results.get("risk_breakdown", {}),
                        risk_factors=results.get("risk_factors", []),
                        model_confidence=results.get("model_confidence", "Low"),
                        days_available=results.get("days_available", 0.0),
                        anomaly_score=float(metrics.get("anomaly_rate_pct", 0.0)) if isinstance(metrics, dict) else 0.0,
                        metadata={
                            "data_completeness_pct": results.get("data_completeness_pct", 100.0),
                            "fallback_mode": results.get("fallback_mode", False),
                            "data_points_analyzed": len(results.get("failure_probability", [])),
                            "sensitivity": params.get("sensitivity", "medium"),
                            "requested_range": {"start_time": requested_start, "end_time": requested_end},
                            "dataset_range": dataset_window,
                        },
                        ensemble=results.get("ensemble"),
                        time_to_failure=results.get("time_to_failure"),
                        reasoning=results.get("reasoning"),
                        degradation_series=results.get("degradation_series"),
                        data_quality_flags=results.get("data_quality_flags"),
                    )

            # ---------------------------------------------------------
            # Permanent JSON safety boundary (NO NaN / NO inf)
            # ---------------------------------------------------------
            safe_results = _json_safe(results)
            safe_metrics = _json_safe(metrics)

            execution_time = int(time.time() - start_clock)

            await self._result_repo.save_results(
                job_id=job_id,
                results=safe_results,
                accuracy_metrics=safe_metrics,
                execution_time_seconds=execution_time,
            )

            await self._result_repo.update_job_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                completed_at=datetime.utcnow(),
                progress=100.0,
                message="Analysis completed successfully",
            )

            self._logger.info(
                "job_completed",
                job_id=job_id,
                execution_time_seconds=execution_time,
            )

        except Exception as e:
            self._logger.error(
                "job_failed",
                job_id=job_id,
                error=str(e),
                exc_info=True,
            )

            # IMPORTANT: always rollback a failed async session
            try:
                await self._result_repo.rollback()
            except Exception:
                pass

            await self._result_repo.update_job_status(
                job_id=job_id,
                status=JobStatus.FAILED,
                completed_at=datetime.utcnow(),
                message="Job failed",
                error_message=str(e),
            )

            raise AnalyticsError(f"Job execution failed: {e}") from e

    async def _latest_accuracy_flag(self, device_id: str) -> Dict[str, Any] | None:
        async with async_session_maker() as session:
            q = (
                select(AccuracyEvaluation)
                .where(AccuracyEvaluation.analysis_type == AnalyticsType.PREDICTION.value)
                .order_by(AccuracyEvaluation.created_at.desc())
                .limit(5)
            )
            rows = list((await session.execute(q)).scalars().all())

        if not rows:
            return {
                "type": "accuracy_certification",
                "is_certified": False,
                "severity": "info",
                "message": "Accuracy not certified yet — ingest labeled events and run /accuracy/evaluate.",
            }

        picked = None
        for row in rows:
            if row.scope_device_id == device_id:
                picked = row
                break
        if picked is None:
            picked = rows[0]

        return {
            "type": "accuracy_certification",
            "is_certified": bool(picked.is_certified),
            "severity": "info" if bool(picked.is_certified) else "warning",
            "message": (
                "Certified against labeled events."
                if bool(picked.is_certified)
                else f"Not certified yet — precision={picked.precision}, recall={picked.recall}, labels={picked.labeled_events}."
            ),
        }

    # ------------------------------------------------------------------
    # Anomaly points
    # ------------------------------------------------------------------

    def _attach_anomaly_points(
        self,
        results: Dict[str, Any],
        df: pd.DataFrame,
    ) -> None:

        def _parse_points(values: Any) -> pd.Series:
            series = pd.Series(values)
            if pd.api.types.is_datetime64_any_dtype(series):
                return pd.to_datetime(series, utc=True, errors="coerce")
            return pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")

        if "timestamp" in df.columns:
            ts_col = "timestamp"
        elif "_time" in df.columns:
            ts_col = "_time"
        else:
            raise AnalyticsError(
                "No timestamp column found in dataset (expected 'timestamp' or '_time')"
            )

        anomaly_scores = results.get("anomaly_score")
        is_anomaly = results.get("is_anomaly")

        if anomaly_scores is None or is_anomaly is None:
            raise AnalyticsError(
                "Anomaly results missing 'anomaly_score' or 'is_anomaly'"
            )

        point_ts = results.get("point_timestamps")
        if isinstance(point_ts, list) and len(point_ts) == len(anomaly_scores):
            timestamps = _parse_points(point_ts)
        else:
            # Fallback: align on min length instead of failing whole job
            n = min(len(df), len(anomaly_scores), len(is_anomaly))
            if n <= 0:
                raise AnalyticsError("No data points available for anomaly point attachment")
            anomaly_scores = anomaly_scores[:n]
            is_anomaly = is_anomaly[:n]
            timestamps = _parse_points(df[ts_col].iloc[:n])

        if timestamps.isna().any():
            raise AnalyticsError(
                "Invalid timestamp values found in dataset"
            )

        points = []

        for ts, score, flag in zip(
            timestamps,
            anomaly_scores,
            is_anomaly,
        ):
            points.append(
                {
                    "timestamp": ts.isoformat(),
                    "anomaly_score": float(score),
                    "is_anomaly": bool(flag),
                }
            )

        results["points"] = points

    # ------------------------------------------------------------------
    # Failure prediction points
    # ------------------------------------------------------------------

    def _attach_failure_points(
        self,
        results: Dict[str, Any],
        df: pd.DataFrame,
    ) -> None:

        if "timestamp" in df.columns:
            ts_col = "timestamp"
        elif "_time" in df.columns:
            ts_col = "_time"
        else:
            raise AnalyticsError(
                "No timestamp column found in dataset (expected 'timestamp' or '_time')"
            )

        failure_prob = results.get("failure_probability")
        predicted = results.get("predicted_failure")
        ttf = results.get("time_to_failure_hours")

        if failure_prob is None or predicted is None or ttf is None:
            raise AnalyticsError(
                "Failure prediction results missing required fields"
            )

        point_ts = results.get("point_timestamps")
        if isinstance(point_ts, list) and len(point_ts) == len(failure_prob):
            timestamps = pd.to_datetime(
                point_ts,
                utc=True,
                errors="coerce",
            )
        else:
            # Fallback: align on min length instead of failing whole job
            n = min(len(df), len(failure_prob), len(predicted), len(ttf))
            if n <= 0:
                raise AnalyticsError("No data points available for failure point attachment")
            failure_prob = failure_prob[:n]
            predicted = predicted[:n]
            ttf = ttf[:n]
            timestamps = pd.to_datetime(
                df[ts_col].iloc[:n],
                utc=True,
                errors="coerce",
            )

        if timestamps.isna().any():
            raise AnalyticsError(
                "Invalid timestamp values found in dataset"
            )

        points = []

        for ts, p, f, h in zip(
            timestamps,
            failure_prob,
            predicted,
            ttf,
        ):
            ttf_val = None
            if h is not None:
                try:
                    ttf_val = float(h)
                except Exception:
                    ttf_val = None
            points.append(
                {
                    "timestamp": ts.isoformat(),
                    "failure_probability": float(p),
                    "predicted_failure": bool(f),
                    "time_to_failure_hours": ttf_val,
                }
            )

        results["points"] = points
