from sklearn.svm import SVC


def make_model():
    return SVC(
        C=10.0,
        gamma=0.001,
        kernel="rbf",
        cache_size=256,
    )
