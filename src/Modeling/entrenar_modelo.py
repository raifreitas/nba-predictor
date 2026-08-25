import sys
from pathlib import Path

import joblib

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from xgboost import XGBClassifier

import config
import features_nba
import db_utils

RUTA_MODELO = config.DIR_MODELOS / "modelo_nba_totales.pkl"
RUTA_CALIBRACION = config.DIR_MODELOS / "calibracion_totales.pkl"
RUTA_COLUMNAS = config.DIR_MODELOS / "columnas_totales.pkl"

GRID = {
    "n_estimators": [200, 350, 500],
    "learning_rate": [0.03, 0.06, 0.1],
    "max_depth": [3, 4, 5],
    "subsample": [0.8, 1.0],
    "colsample_bytree": [0.8, 1.0],
    "min_child_weight": [5, 10, 20],
}


def entrenar():
    X, y, meta = features_nba.construir_dataset()
    if X.empty:
        print("SIN DATOS: cargar GameLog_NBA antes de entrenar")
        return None
    y = y.astype(int)

    corte = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:corte], X.iloc[corte:]
    y_train, y_test = y.iloc[:corte], y.iloc[corte:]

    base = XGBClassifier(
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
        random_state=42,
        n_jobs=4,
    )
    busqueda = RandomizedSearchCV(
        base,
        GRID,
        n_iter=25,
        scoring="roc_auc",
        cv=TimeSeriesSplit(n_splits=5),
        n_jobs=2,
        verbose=0,
        random_state=42,
    )
    busqueda.fit(X_train, y_train)
    mejor = busqueda.best_estimator_

    prob_test = mejor.predict_proba(X_test)[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(prob_test, y_test)

    prob_cal = iso.predict(prob_test)
    acc = accuracy_score(y_test, (prob_cal >= 0.5).astype(int))
    auc = roc_auc_score(y_test, prob_test)

    config.DIR_MODELOS.mkdir(parents=True, exist_ok=True)
    joblib.dump(mejor, RUTA_MODELO)
    joblib.dump(iso, RUTA_CALIBRACION)
    joblib.dump(list(X.columns), RUTA_COLUMNAS)
    db_utils.meta_set("ultimo_entrenamiento", config.ahora_utc())
    db_utils.meta_set("metricas_test", f"acc={acc:.3f}|auc={auc:.3f}|n={len(X)}")

    print(f"Mejores params: {busqueda.best_params_}")
    print(f"Test: {meta['Fecha'].iloc[corte]} -> {meta['Fecha'].iloc[-1]} ({len(X_test)} juegos)")
    print(f"Accuracy calibrada test: {acc:.3f} | ROC AUC: {auc:.3f}")
    print(f"Tasa Over en dataset: {y.mean():.3f} | linea proxy media: {meta['ProxyLinea'].mean():.1f}")
    return mejor


if __name__ == "__main__":
    entrenar()
