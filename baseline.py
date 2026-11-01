from sklearn.linear_model import LogisticRegression


def make_model():
    return LogisticRegression(
        max_iter=1_000,
        solver="lbfgs",
        random_state=0,
    )
