from .models import Relationship


class RelationshipManager:
    def __init__(self):
        self.providers = []

    def register(self, provider):
        self.providers.append(provider)

    def evaluate(self, context):
        relationships = []

        for provider in self.providers:
            relationships.extend(
                provider.collect(context)
            )

        return relationships
