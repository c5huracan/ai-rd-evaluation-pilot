from sklearn.svm import SVC


def make_model():
    return SVC(
        C=10,
        kernel="rbf",
        gamma="scale",
    )
