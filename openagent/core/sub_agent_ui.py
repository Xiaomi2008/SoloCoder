    def _display_sub_agent_ui(self, arguments: dict) -> None:
        """Display visual indicator for sub-agent task."""
        from openagent import bold, cyan, dim, yellow
        
        agent_type = arguments.get("agent_type", "general-purpose")
        description = arguments.get("description", "task")
        
        # Type-specific icon
        icons = {
            "explore": "🔍",
            "plan": "📋",
            "code": "💻",
            "general-purpose": "🤖",
        }
        icon = icons.get(agent_type, "🔧")
        type_name = agent_type.title()
        
        prefix = "  "
        print(prefix + cyan(icon) + " Sub-Agent " + bold(type_name))
        print(prefix + dim("Description:") + " " + description[:100])
        print(prefix + dim("Status:") + " " + yellow("⠋ Processing..."))
        print(prefix + dim("Note:") + " " + cyan("Isolated context to prevent pollution"))
