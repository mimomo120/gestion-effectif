from django.db import models

# Create your models here.
class histo_import(models.Model):
    STATUT_CHOICES = [
        ("EN_COURS", "En cours"),
        ("SUCCES", "Succès"),
        ("ECHEC", "Échec"),
        ("PARTIEL", "Succès partiel"),
    ]

    # --- Champs existants (conservés) ---
    date = models.DateTimeField(auto_now_add=True)
    depar = models.IntegerField(default=0)
    modif = models.IntegerField(default=0)
    supprime = models.IntegerField(default=0)
    erreur = models.IntegerField(default=0)

    # --- Traçabilité ---
    utilisateur = models.CharField(max_length=50, null=True, blank=True)
    nom_fichier = models.CharField(max_length=255, null=True, blank=True)
    fichier = models.FileField(upload_to="imports/", null=True, blank=True)

    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="SUCCES")
    nb_lignes_total = models.PositiveIntegerField(default=0)
    nb_crees = models.PositiveIntegerField(default=0)
    nb_supprimes = models.PositiveIntegerField(default=0)
    nb_ignores = models.PositiveIntegerField(default=0)
    nb_erreurs = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Import dépt {self.depar} - {self.date:%d/%m/%Y %H:%M} - {self.statut}"


class histo_import_detail(models.Model):
    ACTION_CHOICES = [
        ("CREATION", "Création"),
        ("MODIFICATION", "Modification"),
        ("SUPPRESSION", "Suppression"),
        ("IGNORE", "Ignoré"),
        ("ERREUR", "Erreur"),
    ]

    import_parent = models.ForeignKey(
        histo_import, on_delete=models.CASCADE, related_name="details"
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    it = models.CharField(max_length=50, null=True, blank=True)
    matricule = models.CharField(max_length=50, null=True, blank=True)
    nom_complete = models.CharField(max_length=255, null=True, blank=True)

    champ_modifie = models.CharField(max_length=100, null=True, blank=True)
    ancienne_valeur = models.CharField(max_length=255, null=True, blank=True)
    nouvelle_valeur = models.CharField(max_length=255, null=True, blank=True)

    message_erreur = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.action} - {self.it} - {self.champ_modifie}"