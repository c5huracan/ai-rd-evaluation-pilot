from sklearn.svm import SVC


class DigitsModel:
    def __init__(self):
        self.model = SVC(
            C=10.0,
            gamma=0.001,
            kernel="rbf",
            cache_size=256,
            shrinking=True
        )

    def fit(self, X, y):
        self.model.fit(X, y)
        return self

    def predict(self, X):
        return self.model.predict(X)


def make_model():
    return DigitsModel()
