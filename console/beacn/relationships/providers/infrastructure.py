from ..provider import RelationshipProvider


class InfrastructureProvider(
    RelationshipProvider
):

    name = "infrastructure"

    def collect(self, context):

        return []
