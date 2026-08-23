from quality_graph import __version__
from quality_graph.cli import main, parser


def test_version_is_available_through_package_and_cli() -> None:
    assert __version__ == "0.1.0.dev0"
    assert parser().prog == "qg"
    assert main([]) == 0
