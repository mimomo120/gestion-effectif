# models.py (inchangé, fourni pour référence)
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError


class Departement(models.Model):
    nom_departement = models.CharField(max_length=30, unique=True)
    abreviation = models.CharField(max_length=5, unique=True)
    HRBP = models.ForeignKey(
        "Collaborateur", to_field="it",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='departements_RH',
        verbose_name="RH Responsable",
    )
    ADMIN = models.ForeignKey(
        "Collaborateur", to_field="it",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='departement_admine',
        verbose_name="Admin",
    )
    DRH = models.ForeignKey(
        "Collaborateur", to_field="it",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='departements_DRH',
        verbose_name="DRH",
    )
    PILOT = models.ForeignKey(
        "Collaborateur", to_field="it",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='departements_PILOT',
        verbose_name="PILOT",
    )

    class Meta:
        verbose_name = "Département"
        verbose_name_plural = "Départements"

    def __str__(self):
        return self.nom_departement

class Equipe(models.Model):
    nom = models.CharField(max_length=50)
    abreviation = models.CharField(max_length=20, unique=True)
    maquette = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    A = models.IntegerField(default=0, validators=[MinValueValidator(0)])  # A + O
    P = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    C = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    T = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "Équipe"
        verbose_name_plural = "Équipes"

    def __str__(self):
        return self.nom


class Collaborateur(models.Model):
    SEXE_CHOICES = [
        (0, "Femme"),
        (1, "Homme"),
    ]

    it = models.CharField(max_length=8, primary_key=True)  # it = identifiant utilisateur
    matricule = models.CharField(max_length=20, unique=True, null=True, blank=True)
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

    eq = models.CharField(max_length=10 ,default=" ")
    departement = models.ForeignKey(
        Departement,
        to_field="abreviation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collaborateurs",
    )

    class Meta:
        verbose_name = "Collaborateur"
        verbose_name_plural = "Collaborateurs"

    def clean(self):
        if self.ru_it_id and self.ru_it_id == self.it:
            raise ValidationError(
                {"ru_it": "Un collaborateur ne peut pas être son propre responsable (RU)."}
            )

    def __str__(self):
        return self.it

LOT_VERS_CHAMP_MAQUETTE = {
    "A": "A",
    "O": "A",
    "P": "P",
    "E": "T",
    "C": "C",
}
class MaquetteN1(models.Model):
    n1 = models.OneToOneField(
        "Collaborateur.Collaborateur", to_field="it",
        on_delete=models.CASCADE,
        related_name="maquette_n1",
    )
    departement = models.ForeignKey(
        "Collaborateur.Departement", to_field="abreviation",
        on_delete=models.CASCADE,
        related_name="maquettes_n1",
    )
    A = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    T = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    P = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])
    C = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    actif = models.BooleanField(default=True)
    modifie_par = models.ForeignKey(
        "Collaborateur.Collaborateur", to_field="it",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="maquettes_n1_attribuees",
    )
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Maquette N+1"
        verbose_name_plural = "Maquettes N+1"

    @property
    def total(self):
        return self.A + self.T + self.P + self.C

    def __str__(self):
        return f"{self.n1_id} ({self.departement_id}) : {self.total}"

class HistoriqueMaquetteN1(models.Model):
    NATURE_CHOICES = [
        ("MODIFICATION", "Modification manuelle par le PILOT"),
        ("AUTO_CREATION", "Nouveau N+1 détecté (créé à 0)"),
        ("INACTIVATION", "N'est plus N+1 (rôle perdu)"),
        ("REACTIVATION", "Redevenu N+1"),
    ]
    n1 = models.ForeignKey(
        "Collaborateur.Collaborateur", to_field="it",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="historique_maquette",
    )
    n1_it = models.CharField(max_length=8)
    n1_nom = models.CharField(max_length=80, blank=True)
    departement = models.ForeignKey(
        "Collaborateur.Departement", to_field="abreviation",
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    nature = models.CharField(max_length=15, choices=NATURE_CHOICES, default="MODIFICATION")
    ancien_A = models.IntegerField(null=True, blank=True)
    ancien_T = models.IntegerField(null=True, blank=True)
    ancien_P = models.IntegerField(null=True, blank=True)
    ancien_C = models.IntegerField(null=True, blank=True)
    nouveau_A = models.IntegerField(default=0)
    nouveau_T = models.IntegerField(default=0)
    nouveau_P = models.IntegerField(default=0)
    nouveau_C = models.IntegerField(default=0)
    modifie_par = models.ForeignKey(
        "Collaborateur.Collaborateur", to_field="it",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "Historique maquette N+1"
        verbose_name_plural = "Historiques maquette N+1"

    @property
    def ancien_total(self):
        if self.ancien_A is None:
            return None
        return self.ancien_A + self.ancien_T + self.ancien_P + self.ancien_C

    @property
    def nouveau_total(self):
        return self.nouveau_A + self.nouveau_T + self.nouveau_P + self.nouveau_C