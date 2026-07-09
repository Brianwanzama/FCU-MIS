import threading

_thread_locals = threading.local()


def get_current_request():
    """Used by log_action() / signal handlers that need the actor + IP address
    but aren't themselves inside a view function."""
    return getattr(_thread_locals, "request", None)


class CurrentRequestMiddleware:
    """Stashes the current request in a thread-local so model-layer code
    (signals, save() overrides, utils.log_action) can attribute an actor
    and IP address without every call site having to pass request around."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.request = request
        try:
            response = self.get_response(request)
        finally:
            _thread_locals.request = None
        return response
