import { AppErrorName } from '../../types/agent'

interface ErrorBannerProps {
  error: {
    name: AppErrorName
    message: string
    timestamp: Date
  }
  onDismiss: () => void
}

const errorTitles: Record<AppErrorName, string> = {
  NONE: '',
  CONNECTION_ERROR: 'Connection Error',
  AUTH_ERROR: 'Authentication Failed',
  RATE_LIMIT_ERROR: 'Rate Limit Exceeded',
  TIMEOUT_ERROR: 'Request Timeout',
  INTERNAL_ERROR: 'Internal Error',
}

function ErrorBanner({ error, onDismiss }: ErrorBannerProps) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-md p-3 max-w-md mx-auto mt-2 shadow-sm">
      <div className="flex">
        <div className="flex-shrink-0">
          <svg
            className="h-5 w-5 text-red-400"
            viewBox="0 0 20 20"
            fill="currentColor"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
              clipRule="evenodd"
            />
          </svg>
        </div>
        <div className="ml-3">
          <h3 className="text-sm font-medium text-red-800">
            {errorTitles[error.name]}
          </h3>
          <div className="mt-1 text-sm text-red-700">{error.message}</div>
          <div className="mt-2">
            <button
              onClick={onDismiss}
              className="text-sm font-medium text-red-700 hover:text-red-500"
            >
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default ErrorBanner
