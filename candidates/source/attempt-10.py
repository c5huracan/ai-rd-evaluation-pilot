from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC


def make_model():
    return Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", SVC(
            C=10.0,
            gamma="scale",
            kernel="rbf",
            cache_size=256
        )),
    ])
