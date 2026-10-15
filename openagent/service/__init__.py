"""The agent service: a Unix socket in front of the runtime.

The protocol is specified in ``dev_docs/agent-service-protocol.md`` and
implemented in :mod:`openagent.service.protocol`; this package adds the
transport (:mod:`openagent.service.server`), the client that the SwiftUI app
mirrors (:mod:`openagent.service.client`), and the CLI entry points
(:mod:`openagent.service.cli`).
"""
