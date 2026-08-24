from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError


class Departement(models.Model):
    nom_departement = models.CharField(max_length=30, unique=True)
    abreviation = models.CharField(max_length=5, unique=True)
    def __str__(self):
        return self.nom_departement



#Tableau Unite:
class Unite(models.Model):
    nom = models.CharField(max_length=50)
    abreviation = models.CharField(max_length=20, unique=True)

    maquette = models.IntegerField(default=0)
    A = models.IntegerField(default=0)
    P = models.IntegerField(default=0)
    C = models.IntegerField(default=0)
    T = models.IntegerField(default=0)

    def __str__(self):
        return self.nom

#Tableau Collaborateur:
class Collaborateur(models.Model):
    POST_CHOICES = [
        ("O", "Opérateur"),
        ("T", "Team Leader"),
    ]

    SEXE_CHOICES = [
        (0, "Femme"),
        (1, "Homme"),
    ]

    it = models.CharField(max_length=8, primary_key=True)
    matricule = models.CharField(max_length=20, unique=True, null=True, blank=True)
    cin = models.CharField(max_length=8, unique=True, null=True, blank=True)

    nom_complete = models.CharField(max_length=80)

    lot = models.CharField(
        max_length=5,
        choices=[
            ("A", "Anapec"),
            ("O", "CDI"),
            ("P", "Pro"),
            ("E", "Tam"),
            ("C", "Cadre"),
        ],
        blank=True,
    )

    eq = models.CharField(max_length=10, default="Inconnu")

    shift = models.CharField(
        max_length=15,
        choices=[
            ("A", "6-15"),
            ("B", "15-23"),
            ("N", "23-6"),
            ("H 4-8", "4 équipes"),
            ("AD", "Administratif"),
        ],
        null=True,
        blank=True,
    )

    sexe = models.IntegerField(choices=SEXE_CHOICES)

    ru_it = models.ForeignKey(
        "self",
        to_field="it",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subordonnes",
    )

    unite = models.ForeignKey(
        Unite,to_field="abreviation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rus",
    )

    departement = models.ForeignKey(
        "Departement",
        to_field="abreviation",
        on_delete=models.SET_NULL,
        null=True
    )
    def __str__(self):
        return self.it
