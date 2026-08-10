from abc import ABC
from abc import abstractmethod


class RelationshipProvider(ABC):
    name = "unknown"

    @abstractmethod
    def collect(self, context):
        raise NotImplementedError
