interface ToolCallDisplayProps {
  toolName: string
  arguments: Record<string, unknown>
  onDisplay?: (args: Record<string, unknown>) => void
}

function ToolCallDisplay({
  toolName,
  arguments: args,
  onDisplay,
}: ToolCallDisplayProps) {
  const handleDisplay = () => {
    onDisplay?.(args)
  }

  return (
    <div className="mt-3">
      <button
        onClick={handleDisplay}
        className="text-sm text-blue-600 hover:text-blue-800 underline"
      >
        View {toolName} call
      </button>
    </div>
  )
}

export default ToolCallDisplay
