"""Architecture acceptance checks for the migrated package boundaries."""
from tools.check_architecture import check_architecture


def test_architecture_boundaries():
    assert check_architecture() == []
