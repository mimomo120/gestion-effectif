import pandas as pd
from django.db import transaction, IntegrityError
from django.shortcuts import render
from django.contrib import messages
from .forms import ImportFileForm
from Collaborateur.models import Collaborateur, Departement, Unite 
from declaration_effectif.models import historique


def clean_id(value):
    """Nettoie matricule/RU lus par pandas (évite '1467.0')."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def clean_int(value, default=None):
    """Nettoie un compteur numérique lu par pandas (NaN/float -> int).
    Si value est vide et default est None -> renvoie None (pour ne rien écraser)."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_or_create_departement(dpt_code):
    dpt_code = str(dpt_code).strip()
    departement_obj = Departement.objects.filter(abreviation=dpt_code).first()
    if departement_obj:
        return departement_obj
    try:
        return Departement.objects.create(abreviation=dpt_code, nom_departement=dpt_code)
    except IntegrityError:
        return Departement.objects.filter(abreviation=dpt_code).first()


def get_unite(unite_code):
    """Retrouve une Unite existante par son abreviation. Ne la cree jamais
    (le fichier Unite est importe separement) - juste None si introuvable."""
    if not unite_code:
        return None
    return Unite.objects.filter(abreviation=str(unite_code).strip()).first()


def importer_fichier_Collaborateur(request):
    if request.method == "POST":
        form = ImportFileForm(request.POST, request.FILES)
        if form.is_valid():
            fichier = request.FILES["fichier"]

            try:
                if fichier.name.endswith(".csv"):
                    df = pd.read_csv(fichier)
                else:
                    df = pd.read_excel(fichier)
            except Exception as e:
                messages.error(request, f"Impossible de lire le fichier : {e}")
                return render(request, "import_data/import.html", {"form": ImportFileForm()})

            df.columns = df.columns.str.strip()
            df = df.where(pd.notnull(df), None)

            required_fields = ["Utilisateur", "Nom", "Prénom", "Lot"]
            missing_fields = [f for f in required_fields if f not in df.columns]
            if missing_fields:
                messages.error(request, f"Colonnes manquantes: {', '.join(missing_fields)}")
                return render(request, "import_data/import.html", {"form": ImportFileForm()})

            erreurs = []
            crees = 0

            # Une seule grande transaction pour tout
            with transaction.atomic():
                for i, row in df.iterrows():
                    # Chaque ligne a son propre savepoint
                    sid = transaction.savepoint()
                    try:
                        utilisateur = clean_id(row.get("Utilisateur"))
                        if not utilisateur:
                            erreurs.append(f"Ligne {i+2}: Utilisateur manquant")
                            transaction.savepoint_rollback(sid)
                            continue

                        dpt_code = row.get("DPT")
                        departement_obj = None
                        if dpt_code:
                            departement_obj = get_or_create_departement(dpt_code)

                        unite_code = row.get("Unite")
                        unite_obj = None
                        if unite_code is not None and not pd.isna(unite_code):
                            unite_obj = get_unite(unite_code)
                            if not unite_obj:
                                erreurs.append(f"Ligne {i+2}: Unite {unite_code} introuvable")

                        matricule = clean_id(row.get("Matricule"))
                        lot = row.get("Lot", "")
                        post = None
                        if lot == "P":
                            post = "T"
                        elif lot in ("O", "A"):
                            post = "O"

                        matricule_ru = clean_id(row.get("RU"))
                        ru_obj = None
                        if matricule_ru:
                            ru_obj = Collaborateur.objects.filter(matricule=matricule_ru).first()
                            if not ru_obj:
                                erreurs.append(f"Ligne {i+2}: RU matricule {matricule_ru} introuvable")

                        nom = row.get('Nom') or ''
                        prenom = row.get('Prénom') or ''
                        nom_complete = f"{nom} {prenom}".strip()

                        obj, created = Collaborateur.objects.update_or_create(
                            it=utilisateur,
                            defaults={
                                "matricule": matricule,
                                "nom_complete": nom_complete,
                                "lot": lot,
                                "departement": departement_obj,
                                "eq": row.get("Equipe", ""),
                                "shift": row.get("Shift"),
                                "sexe": row.get("Sexe", ""),
                                "post": post,
                                "unite": unite_obj,
                                "ru_it": ru_obj,
                            }
                        )

                        # Valide le savepoint de cette ligne
                        transaction.savepoint_commit(sid)
                        crees += 1

                    except Exception as e:
                        transaction.savepoint_rollback(sid)
                        erreurs.append(f"Ligne {i+2}: {str(e)[:100]}")

            messages.success(request, f"{crees} lignes importées.")
            if erreurs:
                messages.error(request, f"{len(erreurs)} erreurs: " + " | ".join(erreurs[:5]))

            return render(request, "import_data/import.html", {"form": ImportFileForm()})
    else:
        form = ImportFileForm()

    return render(request, "import_data/import.html", {"form": form})

def importer_fichier_Unite(request):
    if request.method == "POST":
        form = ImportFileForm(request.POST, request.FILES)
        if form.is_valid():
            fichier = request.FILES["fichier"]

            try:
                if fichier.name.endswith(".csv"):
                    df = pd.read_csv(fichier)
                else:
                    df = pd.read_excel(fichier)
            except Exception as e:
                messages.error(request, f"Impossible de lire le fichier : {e}")
                return render(request, "import_data/import.html", {"form": ImportFileForm()})

            df.columns = df.columns.str.strip()
            df = df.where(pd.notnull(df), None)

            erreurs = []
            crees = 0

            with transaction.atomic():
                for i, row in df.iterrows():
                    sid = transaction.savepoint()
                    try:
                        abreviation = row.get("abreviation")
                        if not abreviation:
                            erreurs.append(f"Ligne {i+2}: abreviation manquante")
                            transaction.savepoint_rollback(sid)
                            continue

                        existing = Unite.objects.filter(abreviation=abreviation).first()

                        def valeur_ou_existant(champ_fichier, champ_existant_val):
                            v = clean_int(row.get(champ_fichier), default=None)
                            if v is not None:
                                return v
                            return champ_existant_val if champ_existant_val is not None else 0

                        maquette_val = valeur_ou_existant("maquette", existing.maquette if existing else None)
                        a_val = valeur_ou_existant("A", existing.A if existing else None)
                        t_val = valeur_ou_existant("T", existing.T if existing else None)
                        o_val = valeur_ou_existant("O", existing.O if existing else None)
                        p_val = valeur_ou_existant("P", existing.P if existing else None)
                        c_val = valeur_ou_existant("C", existing.C if existing else None)

                        obj, created = Unite.objects.update_or_create(
                            abreviation=abreviation,
                            defaults={
                                "nom": row.get("nom") or abreviation,
                                "maquette": maquette_val,
                                "A": a_val,
                                "T": t_val,
                                "O": o_val,
                                "P": p_val,
                                "C": c_val,
                            }
                        )

                        transaction.savepoint_commit(sid)
                        crees += 1

                    except Exception as e:
                        transaction.savepoint_rollback(sid)
                        erreurs.append(f"Ligne {i+2}: {str(e)[:100]}")

            messages.success(request, f"{crees} lignes importées.")
            if erreurs:
                messages.error(request, f"{len(erreurs)} erreurs: " + " | ".join(erreurs[:5]))

            return render(request, "import_data/import.html", {"form": ImportFileForm()})
    else:
        form = ImportFileForm()

    return render(request, "import_data/import.html", {"form":form})

def importer_fichier_Affectation(request):
    if request.method == "POST":
        form = ImportFileForm(request.POST, request.FILES)
        if form.is_valid():
            fichier = request.FILES["fichier"]

            try:
                if fichier.name.endswith(".csv"):
                    df = pd.read_csv(fichier)
                else:
                    df = pd.read_excel(fichier)
            except Exception as e:
                messages.error(request, f"Impossible de lire le fichier : {e}")
                return render(request, "import_data/import.html", {"form": ImportFileForm()})

            df.columns = df.columns.str.strip()
            df = df.where(pd.notnull(df), None)

            required_fields = ["Nom & prénom", "Ru (Initial)", "RU D'accueil", "Etat"]
            missing_fields = [f for f in required_fields if f not in df.columns]
            if missing_fields:
                messages.error(request, f"Colonnes manquantes: {', '.join(missing_fields)}")
                return render(request, "import_data/import.html", {"form": ImportFileForm()})

            erreurs = []
            crees = 0

            with transaction.atomic():
                for i, row in df.iterrows():
                    sid = transaction.savepoint()
                    try:
                        historique.objects.create(
                            collaborateur=row.get("Nom & prénom") or "",
                            initial=row.get("Ru (Initial)") or "",
                            acceuil=row.get("RU D'accueil") or "",
                            etat=row.get("Etat") or "",
                        )

                        transaction.savepoint_commit(sid)
                        crees += 1

                    except Exception as e:
                        transaction.savepoint_rollback(sid)
                        erreurs.append(f"Ligne {i+2}: {str(e)[:100]}")

            messages.success(request, f"{crees} lignes importées.")
            if erreurs:
                messages.error(request, f"{len(erreurs)} erreurs: " + " | ".join(erreurs[:5]))

            return render(request, "import_data/import.html", {"form": ImportFileForm()})
    else:
        form = ImportFileForm()

    return render(request, "import_data/import.html", {"form": form})