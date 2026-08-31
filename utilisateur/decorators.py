# monapp/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def role_required(*roles_autorises):

    if len(roles_autorises) == 1 and isinstance(roles_autorises[0], (list, tuple, set)):
        roles_autorises = tuple(roles_autorises[0])

    def decorateur(vue_fn):
        @wraps(vue_fn)
        def wrapper(request, *args, **kwargs):
            role = request.session.get("role")
            if role not in roles_autorises:
                messages.error(request, "Vous n'avez pas les droits pour accéder à cette page.")
                return redirect("login") 
            return vue_fn(request, *args, **kwargs)
        return wrapper
    return decorateur