from django.apps import AppConfig
from django.db.models.signals import post_migrate

def create_super_admin(sender, **kwargs):
    from Collaborateur.models import Collaborateur
    from utilisateur.models import utilisateur

    # 1. Créer / Récupérer le collaborateur système avec ses champs obligatoires
    collab, _ = Collaborateur.objects.get_or_create(
        it="1234567",
        defaults={
            "nom_complete": "Administrateur Système",
            "sexe": 1,  # Adapté selon vos choices
            "lot":"C"
        }
    )

    # 2. Créer / Récupérer le compte utilisateur avec TOUS les champs obligatoires dans defaults
    user, created = utilisateur.objects.get_or_create(
        it=collab,
        defaults={
            "role": "SUPER",
            "N1": False,
            "N2": False,
            "N3": False,
            "N4": False,
            "HRBP": False,
            "ADMIN": False,
            "SUPER":True,
        }
    )
    
    # 3. S'assurer que le mot de passe "ms" et les accès ADMIN sont bien configurés
    if created or not user.ADMIN or not user.check_password("oumaima123"):
        user.role = "SUPER"
        user.SUPER = True
        user.SUPER = True
        user.set_password("oumaima123")
        user.save()


class UtilisateurConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'utilisateur'

    def ready(self):
        post_migrate.connect(create_super_admin, sender=self)