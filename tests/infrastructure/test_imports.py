from openagent.core.bash_manager import BashManager as CoreBashManager
from openagent.core.bash_manager import BashSession as CoreBashSession
from openagent.infrastructure import BashManager, BashSession, McpClient
from openagent.infrastructure.bash_manager import (
    BashManager as InfrastructureBashManager,
)
from openagent.infrastructure.bash_manager import (
    BashSession as InfrastructureBashSession,
)
from openagent.infrastructure.mcp import McpClient as InfrastructureMcpClient
from openagent.mcp import McpClient as RootMcpClient


def test_infrastructure_package_exports_existing_runtime_classes() -> None:
    assert BashManager is CoreBashManager
    assert BashSession is CoreBashSession
    assert McpClient is RootMcpClient


def test_infrastructure_modules_reexport_existing_runtime_classes() -> None:
    assert InfrastructureBashManager is CoreBashManager
    assert InfrastructureBashSession is CoreBashSession
    assert InfrastructureMcpClient is RootMcpClient
