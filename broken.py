from sklearn.dummy import DummyClassifier


def make_model():
    return DummyClassifier(strategy="most_frequent")
