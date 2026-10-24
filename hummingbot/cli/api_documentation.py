"""Metadata stays on handlers; request/response schemas stay on their models."""


def api_operation(*, operation_id, summary, tag, response_model, request_model=None, query_model=None,
                  status=200, headers=(), path_parameters=(), response_content_type="application/json", examples=None):
    def annotate(handler):
        handler.api_documentation = dict(operation_id=operation_id, summary=summary, tag=tag,
                                         response_model=response_model, request_model=request_model,
                                         query_model=query_model, status=status, headers=headers,
                                         path_parameters=path_parameters, response_content_type=response_content_type,
                                         examples=examples)
        return handler
    return annotate
