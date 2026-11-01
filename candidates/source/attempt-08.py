from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.svm import SVC


class DigitsModel(BaseEstimator, ClassifierMixin):
    def __init__(self, C=10.0, gamma=0.001):
        self.C = C
        self.gamma = gamma

    def fit(self, X, y):
        self.classes_ = sorted(set(y))
        self.model_ = SVC(
            C=self.C,
            gamma=self.gamma,
            kernel="rbf",
            cache_size=256,
            decision_function_shape="ovr"
        )
        self.model_.fit(X, y)
        return self

    def predict(self, X):
        return self.model_.predict(X)


def make_model():
    return DigitsModel()
