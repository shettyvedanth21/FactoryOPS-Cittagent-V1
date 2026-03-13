"""LSTM autoencoder for anomaly sequence reconstruction."""

import numpy as np

from src.services.analytics.models.tf_runtime import configure_tensorflow_runtime

SEQUENCE_LENGTH = 30
LATENT_DIM = 16
EPOCHS = 50
BATCH_SIZE = 32
THRESHOLD_PCTILE = 95
MIN_SEQUENCES = 50
MAX_TRAIN_SEQUENCES = 12000


class LSTMAnomalyAutoencoder:
    """Sequence model that flags anomalies via reconstruction error."""

    def __init__(self):
        self.model = None
        self.threshold = None
        self.is_trained = False

    def _build(self, n_features: int):
        configure_tensorflow_runtime()
        import tensorflow as tf

        inp = tf.keras.Input(shape=(SEQUENCE_LENGTH, n_features))
        x = tf.keras.layers.LSTM(32, return_sequences=True)(inp)
        x = tf.keras.layers.LSTM(LATENT_DIM)(x)
        x = tf.keras.layers.RepeatVector(SEQUENCE_LENGTH)(x)
        x = tf.keras.layers.LSTM(LATENT_DIM, return_sequences=True)(x)
        x = tf.keras.layers.LSTM(32, return_sequences=True)(x)
        out = tf.keras.layers.TimeDistributed(tf.keras.layers.Dense(n_features))(x)

        self.model = tf.keras.Model(inp, out)
        self.model.compile(optimizer="adam", loss="mse")

    def train(self, sequences: np.ndarray) -> bool:
        if len(sequences) < MIN_SEQUENCES:
            self.is_trained = False
            return False

        try:
            import tensorflow as tf
        except Exception:
            self.is_trained = False
            return False

        configure_tensorflow_runtime()
        self._build(sequences.shape[2])
        train_seq = sequences[-MAX_TRAIN_SEQUENCES:] if len(sequences) > MAX_TRAIN_SEQUENCES else sequences
        batch_size = min(BATCH_SIZE, max(8, len(train_seq) // 20))
        self.model.fit(
            train_seq,
            train_seq,
            epochs=EPOCHS,
            batch_size=batch_size,
            validation_split=0.1,
            verbose=0,
            callbacks=[
                tf.keras.callbacks.EarlyStopping(
                    patience=5,
                    restore_best_weights=True,
                )
            ],
        )

        preds = self.model.predict(train_seq, verbose=0, batch_size=batch_size)
        errors = np.mean(np.abs(train_seq - preds), axis=(1, 2))
        self.threshold = float(np.percentile(errors, THRESHOLD_PCTILE))
        self.is_trained = True
        return True

    def predict(self, sequences: np.ndarray) -> dict:
        n = len(sequences)
        if not self.is_trained or self.model is None or n == 0:
            return {
                "is_anomaly": np.zeros(n, dtype=bool),
                "anomaly_score": np.zeros(n),
                "reconstruction_error": np.zeros(n),
                "threshold": 0.0,
                "is_trained": False,
            }

        batch_size = min(BATCH_SIZE, max(8, len(sequences) // 20))
        preds = self.model.predict(sequences, verbose=0, batch_size=batch_size)
        errors = np.mean(np.abs(sequences - preds), axis=(1, 2))
        scores = np.clip(errors / (self.threshold + 1e-9) / 3, 0, 1)

        return {
            "is_anomaly": errors > self.threshold,
            "anomaly_score": scores,
            "reconstruction_error": errors,
            "threshold": self.threshold,
            "is_trained": True,
        }
