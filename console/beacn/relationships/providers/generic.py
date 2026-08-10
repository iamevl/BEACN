from ..provider import RelationshipProvider


class GenericProvider(
    RelationshipProvider
):

    name = "generic"

    def collect(self, context):

        return []
