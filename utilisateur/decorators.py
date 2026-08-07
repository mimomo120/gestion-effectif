# monapp/decorators.py
from functools import wraps
from django.shortcuts import redirect

def role_required(*roles_autorises):
    def decorateur(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            role = request.session.get('role')

            if not role:
                return redirect('login')
            if role not in roles_autorises:
                return redirect('login')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorateur