import time
from sklearn.dummy import DummyClassifier

def make_model():
        time.sleep(5)
            return DummyClassifier(strategy="most_frequent")
