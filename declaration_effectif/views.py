from django.shortcuts import render, redirect, get_object_or_404
from utilisateur.models import utilisateur
from Collaborateur.models import (Departement, Collaborateur, Equipe, MaquetteN1)
from django.contrib.auth.hashers import make_password, check_password
from django.db.models import Q, Count, Sum, Max ,F
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif, Alert, historique
from django.http import JsonResponse
from datetime import date, datetime, timedelta
import json
from django.db import transaction, IntegrityError
from django.views.decorators.csrf import ensure_csrf_cookie
from collections import defaultdict
from Collaborateur.views import (
    rec, Ru_Rg, liste_N1_par_N2, Rg_Dur, liste_N1_pr_N3, liste_N3_N4,
    SystEff, reelEff, get_tous_les_it_sous, get_tous_les_n1,
    get_n1_et_n2_sous, get_managers_its, invalider_cache_hierarchie,
    get_maquettes_n1_map, get_managers_avec_operateurs_aop, niveau_hierarchique,
    get_hierarchie_map, repartir_maquette_par_lot,
)
from django.views.decorators.http import require_POST
from utilisateur.decorators import role_required
from django.utils.dateparse import parse_date
from django.core.paginator import PageNotAnInteger, Paginator, EmptyPage
import bisect
from django.views.decorators.cache import never_cache
from django.contrib.auth.decorators import login_required


@ensure_csrf_cookie
def valider(request):
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON invalide"}, status=400)

    it = request.session.get("it")
    if not it:
        return JsonResponse({"error": "Session expirée."}, status=401)

    liste_valides = data.get("valides", [])
    liste_refuses = data.get("changement", [])
    liste_depart = data.get("depart", [])
    liste_ajouter = data.get("ajouter", [])

    try:
        with transaction.atomic():
            for operateur in liste_valides:
                collaborateur_obj = Collaborateur.objects.get(it=operateur)
                declaration_effectif.objects.create(
                    collaborateur_it=collaborateur_obj, Ru_id=it, nature="V"
                )
            for operateur in liste_refuses:
                collaborateur_obj = Collaborateur.objects.get(it=operateur["it"])
                nv_ru_obj = Collaborateur.objects.get(it=operateur["nvRu"])
                declaration_effectif.objects.create(
                    collaborateur_it=collaborateur_obj, Ru_id=it,
                    nv_Ru=nv_ru_obj, nature="C"
                )
            for operateur in liste_depart:
                collaborateur_obj = Collaborateur.objects.get(it=operateur)
                declaration_effectif.objects.create(
                    collaborateur_it=collaborateur_obj, Ru_id=it, nature="D"
                )
            for operateur in liste_ajouter:
                collaborateur_obj = Collaborateur.objects.get(it=operateur)
                declaration_effectif.objects.create(
                    collaborateur_it=collaborateur_obj, Ru_id=it, nature="A"
                )
    except (IntegrityError, KeyError, Collaborateur.DoesNotExist):
        return JsonResponse({"error": "Erreur lors de l'enregistrement.", "status": "erreur"}, status=400)

    invalider_cache_hierarchie()
    return JsonResponse({"status": "valider"})


def calculer_niveaux_hierarchie():
    tous_ru_it = get_managers_its()
    mapping = get_hierarchie_map()
    managers_aop = get_managers_avec_operateurs_aop()
    cache_niveau = {}

    for manager_it in tous_ru_it:
        niveau_hierarchique(
            manager_it, tous_ru_it, mapping, cache_niveau,
            managers_aop=managers_aop,
        )

    niveau_par_it = {
        it: min(n, 4) for it, n in cache_niveau.items() if n and n >= 1
    }

    l1 = {it for it, n in niveau_par_it.items() if n == 1}
    l2 = {it for it, n in niveau_par_it.items() if n == 2}
    l3 = {it for it, n in niveau_par_it.items() if n == 3}
    l4 = {it for it, n in niveau_par_it.items() if n == 4}

    return niveau_par_it, l1, l2, l3, l4


def determiner_hierarchie(it_val):
    niveau_par_it, l1, l2, l3, l4 = calculer_niveaux_hierarchie()

    if it_val in l1:
        role_calcule = "N+1"
        n1_flag, n2_flag, n3_flag, n4_flag = 1, 0, 0, 0
    elif it_val in l2:
        role_calcule = "N+2"
        n1_flag, n2_flag, n3_flag, n4_flag = 0, 1, 0, 0
    elif it_val in l3:
        role_calcule = "N+3"
        n1_flag, n2_flag, n3_flag, n4_flag = 0, 0, 1, 0
    elif it_val in l4:
        role_calcule = "N+4"
        n1_flag, n2_flag, n3_flag, n4_flag = 0, 0, 0, 1
    else:
        role_calcule = None
        n1_flag, n2_flag, n3_flag, n4_flag = 0, 0, 0, 0

    return role_calcule, n1_flag, n2_flag, n3_flag, n4_flag


def register_view(request):
    if request.method == "POST":
        it = request.POST.get('it')
        password = request.POST.get('password')

        if not it or not password:
            return render(request, "utilisateur/register.html", {
                'message': "Vous devez remplir tous les champs."
            })

        try:
            col = Collaborateur.objects.get(it=it)
        except Collaborateur.DoesNotExist:
            return render(request, "utilisateur/register.html", {
                'message': "Ce collaborateur n'existe pas dans la base de données."
            })

        if utilisateur.objects.filter(it=col).exists():
            return render(request, "utilisateur/register.html", {
                'message': "Cet utilisateur est déjà enregistré."
            })

        role_calcule, n1, n2, n3, n4 = determiner_hierarchie(it)

        if not role_calcule:
            return render(request, "utilisateur/accesInterdi.html")

        util, created = utilisateur.objects.update_or_create(
            it=col,
            defaults={
                "role": role_calcule,
                "password": make_password(password),
                "N1": n1, "N2": n2, "N3": n3, "N4": n4,
                "ADMIN": 0, "HRBP": 0, "SUPER": 0, "DRH": 0, "PILOT": 0,
            }
        )
        return render(request, "utilisateur/login.html")

    return render(request, "utilisateur/register.html")


@role_required('N+1')
def validation_view(request):
    it = request.session.get("it")
    if not it:
        return render(request, 'declaration_effectif/N1/Validation.html', {
            "operateurs_finaux": [], "nbr": 0, "status": False, "date": timezone.localdate()
        })

    directs_qs = Collaborateur.objects.filter(ru_it_id=it).exclude(it=it)
    direct_ids = list(directs_qs.values_list('it', flat=True))

    manager_direct_ids = set(
        Collaborateur.objects.filter(ru_it_id__in=direct_ids)
        .values_list('ru_it_id', flat=True)
        .distinct()
    )
    manager_direct_its = set(
        Collaborateur.objects.filter(it__in=manager_direct_ids)
        .values_list('it', flat=True)
    )

    aujourdhui = timezone.localdate()
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()

    if der and der.date == aujourdhui:
        operateurs_finaux_qs = declaration_effectif.objects.filter(Ru_id=it, date=aujourdhui)
        operateurs_finaux_qs = operateurs_finaux_qs.exclude(collaborateur_it_id__in=manager_direct_its)
        status = True
        operateurs_finaux = operateurs_finaux_qs.exclude(collaborateur_it__lot__in=["C", "E"])
    else:
        candidats = rec(request)
        if hasattr(candidats, 'exclude'):
            operateurs_finaux = candidats.exclude(it__in=manager_direct_its).exclude(lot__in=["C", "E"])
        else:
            operateurs_finaux = [
                c for c in candidats
                if getattr(c, 'it', None) not in manager_direct_its
                and getattr(c, 'lot', None) not in ["C", "E"]
            ]
        status = False

    nbr = operateurs_finaux.count() if hasattr(operateurs_finaux, 'count') else len(operateurs_finaux)

    return render(
        request, 'declaration_effectif/N1/Validation.html',
        {"operateurs_finaux": operateurs_finaux, "nbr": nbr, "status": status, "date": aujourdhui}
    )


def difference(request):
    it = request.session.get("it")
    operateur_systeme = SystEff(it)
    liste_s = set(operateur_systeme.values_list("it", flat=True))

    operateur_reel = reelEff(it)
    if operateur_reel:
        liste_r = set(operateur_reel.values_list("it", flat=True))
        r = liste_r - liste_s
        s = liste_s - liste_r
        return {
            "systeme1": Collaborateur.objects.filter(it__in=s),
            "reel1": Collaborateur.objects.filter(it__in=r),
        }

    return {"systeme1": Collaborateur.objects.none(), "reel1": Collaborateur.objects.none()}


def histo(ut):
    if not ut:
        return {"declarations": [], "nbr": 0}

    resp = Collaborateur.objects.filter(it=ut).first()
    if not resp:
        return {"declarations": [], "nbr": 0}

    declarations = historique.objects.filter(
        Q(initial=resp.nom_complete) & ~Q(etat="Terminé")
    )
    return {"declarations": declarations, "nbr": declarations.count()}


def historique_pour(liste_its):
    liste_its = list(liste_its)
    if not liste_its:
        return historique.objects.none()

    noms = set(
        Collaborateur.objects.filter(it__in=liste_its).values_list("nom_complete", flat=True)
    )
    if not noms:
        return historique.objects.none()

    return historique.objects.filter(initial__in=noms).exclude(etat="Terminé")


@role_required('N+1')
def histo_aff(request):
    util = request.session.get("it")
    changements = histo(util)
    declarations = changements["declarations"]

    status = request.GET.get("status", "all")
    if status == "valide":
        declarations = declarations.filter(etat__icontains="valid")
    elif status == "refuse":
        declarations = declarations.filter(etat__icontains="refus")
    elif status == "non_demarrer":
        declarations = declarations.filter(etat__icontains="non démarr")
    paginator = Paginator(declarations, 15)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "declaration_effectif/N1/Affectations_historique.html", {
        "info": page_obj, "page_obj": page_obj, "nbr": paginator.count, "status": status,
    })


def supprimer(request):
    it = request.session.get("it")
    if not it:
        return JsonResponse({"status": "erreur", "error": "Session invalide"}, status=401)

    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    if der:
        declaration_effectif.objects.filter(Ru_id=it, date=der.date).delete()
        invalider_cache_hierarchie()
        return JsonResponse({"status": "supprimer"})

    return JsonResponse({"status": "erreur", "error": "Aucune déclaration à supprimer"}, status=404)


def afficher_modifier(request):
    it = request.session.get("it")
    der = declaration_effectif.objects.filter(Ru_id=it).order_by("-date").first()
    aujourdhui = timezone.localdate()
    return JsonResponse({"valide": bool(der and der.date == aujourdhui)})


@role_required('N+2')
def liste_N1_non_valides_N2(request):
    maint = timezone.localdate()
    it = request.session.get("it")
    liste_ru = Ru_Rg(it)
    liste_n1_ids = set(liste_ru.values_list("it", flat=True))

    declaration = declaration_effectif.objects.filter(date=maint, Ru_id__in=liste_n1_ids)
    liste_Ru = set(declaration.values_list("Ru_id", flat=True))

    liste_ru_avec_operateurs = set(
        Collaborateur.objects.filter(ru_it_id__in=liste_n1_ids, lot__in=["A", "O", "P"])
        .values_list("ru_it_id", flat=True)
        .distinct()
    )
    ru_non_valides_ids = liste_ru_avec_operateurs - liste_Ru
    non_valides = liste_ru.filter(it__in=ru_non_valides_ids).distinct()

    return render(request, "declaration_effectif/N2/validation.html", {"non_valides": non_valides, "date": maint})


@role_required('N+2')
def affectation_N1(request):
    util = request.session.get("it")
    status = request.GET.get("status", "all")
    ru_init = request.GET.get("ru_init", "").strip()
    ru_acceuil = request.GET.get("ru_acceuil", "").strip()

    changements = histo(util)
    toutes_declarations_N2 = list(changements["declarations"])

    n1 = Ru_Rg(util)
    n1_ids = list(n1.values_list("it", flat=True))
    toutes_declarations = list(historique_pour(n1_ids))

    tab_actif = request.GET.get("tab", "tab-mes")
    ensemble_onglet = toutes_declarations if tab_actif == "tab-toutes" else toutes_declarations_N2
    ru_initiaux = sorted({d.initial for d in ensemble_onglet if d.initial})
    ru_acceuils = sorted({d.acceuil for d in ensemble_onglet if d.acceuil})

    def _filtrer(liste, mot_cle):
        return [d for d in liste if d.etat and mot_cle in str(d.etat).lower()]

    if status == "valide":
        toutes_declarations_N2 = _filtrer(toutes_declarations_N2, "valid")
        toutes_declarations = _filtrer(toutes_declarations, "valid")
    elif status == "refuse":
        toutes_declarations_N2 = _filtrer(toutes_declarations_N2, "refus")
        toutes_declarations = _filtrer(toutes_declarations, "refus")
    elif status == "non_demarrer":
        toutes_declarations_N2 = _filtrer(toutes_declarations_N2, "non démarr")
        toutes_declarations = _filtrer(toutes_declarations, "non démarr")

    if ru_init:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.initial == ru_init]
        toutes_declarations = [d for d in toutes_declarations if d.initial == ru_init]
    if ru_acceuil:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.acceuil == ru_acceuil]
        toutes_declarations = [d for d in toutes_declarations if d.acceuil == ru_acceuil]

    paginator_n1 = Paginator(toutes_declarations, 10)
    page_obj_n1 = paginator_n1.get_page(request.GET.get("page_n1", 1))
    paginator_n2 = Paginator(toutes_declarations_N2, 10)
    page_obj_n2 = paginator_n2.get_page(request.GET.get("page_n2", 1))

    return render(request, "declaration_effectif/N2/affectation.html", {
        "n2": page_obj_n2, "page_obj_n2": page_obj_n2,
        "info": page_obj_n1, "page_obj_n1": page_obj_n1,
        "nbr": len(toutes_declarations), "nbr2": len(toutes_declarations_N2),
        "status": status, "ru_init": ru_init, "ru_acceuil": ru_acceuil,
        "ru_initiaux": ru_initiaux, "ru_acceuils": ru_acceuils,
    })


@role_required('N+3')
def liste_N1_non_valides_N3(request):
    maint = timezone.localdate()
    it = request.session.get("it")
    n1_ids = get_tous_les_n1(it)

    liste_declares = set(
        declaration_effectif.objects.filter(date=maint, Ru_id__in=n1_ids).values_list("Ru_id", flat=True)
    )
    non_valides = Collaborateur.objects.filter(it__in=n1_ids).exclude(it__in=liste_declares)

    return render(request, "declaration_effectif/N3/validation.html", {"non_valides": non_valides, "date": maint})


@role_required('N+3')
def affectation_N3(request):
    util = request.session.get("it")
    status = request.GET.get("status", "all")
    ru_init = request.GET.get("ru_init", "").strip()
    ru_acceuil = request.GET.get("ru_acceuil", "").strip()
    n1_ids, n2_ids = get_n1_et_n2_sous(util)

    try:
        user = Collaborateur.objects.get(it=util)
    except Collaborateur.DoesNotExist:
        user = None

    if user and user.nom_complete:
        nom_user = user.nom_complete.strip()
        toutes_declarations_N2 = historique.objects.filter(
            Q(initial__iexact=nom_user) | Q(acceuil__iexact=nom_user)
        ).exclude(etat="Terminé")
    else:
        toutes_declarations_N2 = historique.objects.none()

    toutes_declarations = list(historique_pour(n2_ids)) + list(historique_pour(n1_ids))

    tab_actif = request.GET.get("tab", "tab-mes")
    ensemble_onglet = toutes_declarations if tab_actif == "tab-toutes" else toutes_declarations_N2
    ru_initiaux = sorted({d.initial for d in ensemble_onglet if d.initial})
    ru_acceuils = sorted({d.acceuil for d in ensemble_onglet if d.acceuil})

    def _filtrer(liste, mot_cle):
        return [d for d in liste if d.etat and mot_cle in str(d.etat).lower()]

    if status == "valide":
        toutes_declarations_N2 = _filtrer(toutes_declarations_N2, "valid")
        toutes_declarations = _filtrer(toutes_declarations, "valid")
    elif status == "refuse":
        toutes_declarations_N2 = _filtrer(toutes_declarations_N2, "refus")
        toutes_declarations = _filtrer(toutes_declarations, "refus")
    elif status == "non_demarrer":
        toutes_declarations_N2 = _filtrer(toutes_declarations_N2, "non démarr")
        toutes_declarations = _filtrer(toutes_declarations, "non démarr")

    if ru_init:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.initial == ru_init]
        toutes_declarations = [d for d in toutes_declarations if d.initial == ru_init]
    if ru_acceuil:
        toutes_declarations_N2 = [d for d in toutes_declarations_N2 if d.acceuil == ru_acceuil]
        toutes_declarations = [d for d in toutes_declarations if d.acceuil == ru_acceuil]

    paginator_n1 = Paginator(toutes_declarations, 10)
    page_obj_n1 = paginator_n1.get_page(request.GET.get("page_n1", 1))
    paginator_n2 = Paginator(toutes_declarations_N2, 10)
    page_obj_n2 = paginator_n2.get_page(request.GET.get("page_n2", 1))

    return render(request, "declaration_effectif/N2/affectation.html", {
        "n2": page_obj_n2, "page_obj_n2": page_obj_n2,
        "info": page_obj_n1, "page_obj_n1": page_obj_n1,
        "nbr": len(toutes_declarations), "nbr2": len(toutes_declarations_N2),
        "status": status, "ru_init": ru_init, "ru_acceuil": ru_acceuil,
        "ru_initiaux": ru_initiaux, "ru_acceuils": ru_acceuils,
        "tab_actif": tab_actif,
    })


def envoyer_alert(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Requête invalide."}, status=400)

    ru_it = data.get("ru_it")
    it = request.session.get("it")

    if not ru_it:
        return JsonResponse({"status": "error", "message": "Matricule du RU manquant."}, status=400)
    if not it:
        return JsonResponse({"status": "error", "message": "Utilisateur non authentifié."}, status=401)

    emetteur_obj = get_object_or_404(Collaborateur, it=it)
    recepteur_obj = get_object_or_404(Collaborateur, it=ru_it)

    Alert.objects.create(
        emetteur=emetteur_obj, recepteur=recepteur_obj,
        contenu="Vous n'avez pas déclaré votre liste des effectifs pour aujourd'hui.",
        lu=0,
    )
    return JsonResponse({"status": "ok", "message": "Alerte envoyée."})


# ------------------------------------------------------------------
# OPTIMISATION : équivalents "batchés" de SystEff() et reelEff() pour
# PLUSIEURS RU à la fois, AVEC la répartition par lot incluse.
# ------------------------------------------------------------------

def _systeme_effectif_batch(ru_ids, managers_its):
    """
    Équivalent batché de SystEff() pour plusieurs RU.
    Retourne (total_par_ru, lot_par_ru) :
      - total_par_ru = {ru_id: count}
      - lot_par_ru   = {ru_id: {lot: count}}
    """
    ru_ids = list(ru_ids)
    if not ru_ids:
        return {}, {}

    qs = (
        Collaborateur.objects.filter(ru_it_id__in=ru_ids)
        .exclude(it__in=managers_its)
        .exclude(it=F('ru_it_id'))
        .values('ru_it_id', 'lot')
        .annotate(total=Count('it', distinct=True))
    )

    total_par_ru = defaultdict(int)
    lot_par_ru = defaultdict(dict)
    for row in qs:
        ru_id, lot_val, total = row['ru_it_id'], row['lot'], row['total']
        total_par_ru[ru_id] += total
        lot_par_ru[ru_id][lot_val] = lot_par_ru[ru_id].get(lot_val, 0) + total

    return dict(total_par_ru), dict(lot_par_ru)


def _reel_effectif_batch(ru_ids):
    """
    Équivalent batché de reelEff() (sans date_reference) pour plusieurs
    RU. Reproduit fidèlement la logique de Collaborateur.views.reelEff().
    Retourne {ru_id: set(collaborateur_it)}.
    """
    ru_ids = list(ru_ids)
    if not ru_ids:
        return {}

    membres_par_ru = defaultdict(set)
    for c_it, c_ru_it in (
        Collaborateur.objects.filter(ru_it_id__in=ru_ids)
        .exclude(it=F('ru_it_id'))
        .values_list('it', 'ru_it_id')
    ):
        membres_par_ru[c_ru_it].add(c_it)

    dernieres_dates = dict(
        declaration_effectif.objects.filter(Ru_id__in=ru_ids)
        .values('Ru_id').annotate(d=Max('date')).values_list('Ru_id', 'd')
    )

    exclus_par_ru = defaultdict(set)
    for ru_id, collab_id in (
        declaration_effectif.objects.filter(Ru_id__in=ru_ids, nature__in=["C", "D"])
        .values_list('Ru_id', 'collaborateur_it_id')
    ):
        exclus_par_ru[ru_id].add(collab_id)

    ajouts_valides_par_ru = defaultdict(set)
    for ru_id, collab_id, dt in (
        declaration_effectif.objects.filter(Ru_id__in=ru_ids, nature__in=["A", "V"])
        .values_list('Ru_id', 'collaborateur_it_id', 'date')
    ):
        if dernieres_dates.get(ru_id) == dt:
            ajouts_valides_par_ru[ru_id].add(collab_id)

    resultat = {}
    for ru_id in ru_ids:
        if ru_id in dernieres_dates:
            base = membres_par_ru.get(ru_id, set()) - exclus_par_ru.get(ru_id, set())
            resultat[ru_id] = (base | ajouts_valides_par_ru.get(ru_id, set())) - {ru_id}
        else:
            resultat[ru_id] = membres_par_ru.get(ru_id, set())
    return resultat


def _reel_effectif_lot_batch(ru_ids):
    """
    Comme _reel_effectif_batch(), mais ramène aussi la répartition par
    lot : (total_par_ru, lot_par_ru), avec lot_par_ru = {ru_id: {lot: count}}.
    """
    reel_sets = _reel_effectif_batch(ru_ids)

    tous_collab_ids = set()
    for s in reel_sets.values():
        tous_collab_ids |= s

    lot_map = dict(
        Collaborateur.objects.filter(it__in=tous_collab_ids).values_list('it', 'lot')
    )

    total_par_ru = {}
    lot_par_ru = {}
    for ru_id, collabs in reel_sets.items():
        total_par_ru[ru_id] = len(collabs)
        compte_lot = defaultdict(int)
        for c_it in collabs:
            compte_lot[lot_map.get(c_it)] += 1
        lot_par_ru[ru_id] = dict(compte_lot)

    return total_par_ru, lot_par_ru


@role_required('N+3')
def dashboard_N3(request):
    it_session_original = request.session.get("it")
    if not it_session_original:
        return redirect("login")

    tous_ru_it = get_managers_its()
    mapping = get_hierarchie_map()
    niveau_par_it_local = {}
    for manager_it in tous_ru_it:
        niveau_hierarchique(
            manager_it, tous_ru_it, mapping, niveau_par_it_local,
            managers_aop=tous_ru_it,
        )
    niveau_par_it = {it: min(n, 4) for it, n in niveau_par_it_local.items() if n and n >= 1}

    l1 = {it for it, n in niveau_par_it.items() if n == 1}
    l2 = {it for it, n in niveau_par_it.items() if n == 2}
    l3 = {it for it, n in niveau_par_it.items() if n == 3}
    l4 = {it for it, n in niveau_par_it.items() if n == 4}
    tous_managers_classes = l1 | l2 | l3 | l4

    directs_n3 = Collaborateur.objects.filter(ru_it_id=it_session_original)
    managers_directs_n3 = [c for c in directs_n3 if c.it in tous_managers_classes]
    operateurs_directs_n3 = [c for c in directs_n3 if c.it not in tous_managers_classes]

    n1_directs = [c for c in managers_directs_n3 if niveau_par_it.get(c.it) == 1]
    n2_list = [c for c in managers_directs_n3 if niveau_par_it.get(c.it) == 2]

    def collab_niveau(m_it, niveau_cible):
        return [c for c in Collaborateur.objects.filter(ru_it_id=m_it) if niveau_par_it.get(c.it) == niveau_cible]

    def operateurs_directs_de(m_it):
        return [c for c in Collaborateur.objects.filter(ru_it_id=m_it) if c.it not in tous_managers_classes]
    
    n2_to_n1 = {n2.it: collab_niveau(n2.it, 1) for n2 in n2_list}
    n2_to_operateurs = {n2.it: operateurs_directs_de(n2.it) for n2 in n2_list}
    it_n2 = {c.it for c in n2_list}

    it_n1_set = {c.it for c in n1_directs}
    for sous_managers in n2_to_n1.values():
        it_n1_set.update(c.it for c in sous_managers)
    it_n1 = list(it_n1_set)

    nbr_n1_total = len(it_n1)
    nbr_n2_total = len(n2_list)
    total_operateurs_directs_n2 = sum(len(v) for v in n2_to_operateurs.values())

    if not it_n1 and not operateurs_directs_n3 and not total_operateurs_directs_n2:
        return render(request, "declaration_effectif/N3/dashboard.html", {
            "liste_ru_stats": [], "total_r": 0, "total_syst": 0, "MR": 0, "MS": 0,
            "maquette_totale": 0, "maint": timezone.localdate(),
            "non_valides": 0, "operateurs_directs_n3": 0,
            "n1_directs": [], "n2_list": [], "nbr_n1_total": 0, "nbr_n2_total": 0,
            "chart_labels": [], "chart_data": [], "labels_ru": [], "data_reel": [],
            "data_systeme": [], "data_maquette": [],
            "lot_labels": [], "lot_reel": [], "lot_systeme": [], "lot_maquette": [], "lot_details": {},
            "liste_departs": [], "liste_changements_non_faits": [],
            "nbr_departs": 0, "nbr_changements_non_faits": 0,
            "somme_ap": 0, "somme_ce": 0,
        })

    today = timezone.now().date()
    start = today - timedelta(days=6)
    dates = [start + timedelta(days=i) for i in range(7)]
    labels_list = [d.strftime('%d %b') for d in dates]

    collaborateurs_n1 = Collaborateur.objects.filter(it__in=it_n1).exclude(it__in=it_n2)
    maquettes_n1_map = get_maquettes_n1_map(it_n1)

    # ---- OPTIMISATION : systeme/reel (total ET par lot) calculés EN BLOC
    # pour tous les N1 d'un coup -- réutilisés plus bas dans la boucle
    # "répartition par lot". ----
    it_n1_liste = [c.it for c in collaborateurs_n1]
    systeme_total, systeme_lot = _systeme_effectif_batch(it_n1_liste, tous_ru_it)
    reel_total, reel_lot = _reel_effectif_lot_batch(it_n1_liste)
    dernieres_dates_av = dict(
        declaration_effectif.objects.filter(Ru_id__in=it_n1_liste, nature__in=["A", "V"])
        .values('Ru_id').annotate(d=Max('date')).values_list('Ru_id', 'd')
    )

    liste_ru_stats = []
    maint_global = None

    for collab in collaborateurs_n1:
        ru_it = collab.it
        systeme = systeme_total.get(ru_it, 0)
        reel = reel_total.get(ru_it, 0)
        date_ref = dernieres_dates_av.get(ru_it)

        maquette_obj = maquettes_n1_map.get(collab.it)
        maquette_brute = maquette_obj.total if maquette_obj else 0

        liste_ru_stats.append({
            "n1": collab,
            "matricule": getattr(collab, "matricule", None),
            "nom_complete": getattr(collab, "nom_complete", ""),
            "eq": getattr(collab, 'eq_id', None),
            "reel": reel, "systeme": systeme, "maquette": maquette_brute,
            "mr": reel - maquette_brute, "ms": systeme - maquette_brute,
            "last_decl_date": date_ref,
        })

        if date_ref and (maint_global is None or date_ref > maint_global):
            maint_global = date_ref

    if maint_global is None:
        maint_global = timezone.localdate()

    tous_les_ru_ids = list(set(it_n1))
    liste_declares_today = set(
        declaration_effectif.objects.filter(date=today, Ru_id__in=tous_les_ru_ids)
        .values_list('Ru_id', flat=True)
    )
    non_valides = sum(
        1 for stat in liste_ru_stats
        if stat["systeme"] > 0 and stat["n1"].it not in liste_declares_today
    )

    total_r = (
        sum(stat["reel"] for stat in liste_ru_stats)
        + len(operateurs_directs_n3) + total_operateurs_directs_n2
        + nbr_n1_total + nbr_n2_total
    )
    total_syst = (
        sum(stat["systeme"] for stat in liste_ru_stats)
        + len(operateurs_directs_n3) + total_operateurs_directs_n2
        + nbr_n1_total + nbr_n2_total
    )

    operateurs_systeme_session = SystEff(it_session_original, managers_its=tous_ru_it)
    syste_session = operateurs_systeme_session.values('it').distinct().count()

    decl_window_qs = (
        declaration_effectif.objects
        .filter(Ru_id__in=it_n1, nature__in=["A", "V"], date__gte=start, date__lte=today)
        .values('Ru_id', 'date')
        .annotate(total=Count('collaborateur_it_id', distinct=True))
    )
    decls_by_ru = {}
    for r in decl_window_qs:
        decls_by_ru.setdefault(r['Ru_id'], []).append((r['date'], r['total']))
    for ru_key in decls_by_ru:
        decls_by_ru[ru_key].sort()

    part_fixe_par_jour = len(operateurs_directs_n3) + total_operateurs_directs_n2 + nbr_n1_total + nbr_n2_total
    data_totale_par_jour = [part_fixe_par_jour] * len(dates)
    stat_dict = {stat["n1"].it: stat["systeme"] for stat in liste_ru_stats}

    for ru_it_key in [c.it for c in collaborateurs_n1]:
        ru_decls = decls_by_ru.get(ru_it_key, [])
        ru_system = stat_dict.get(ru_it_key, 0)
        ru_dates = [dt for dt, _ in ru_decls]
        ru_totals = [t for _, t in ru_decls]
        for idx, d in enumerate(dates):
            if ru_dates:
                pos = bisect.bisect_right(ru_dates, d) - 1
                if pos >= 0:
                    data_totale_par_jour[idx] += ru_totals[pos]
                    continue
            data_totale_par_jour[idx] += ru_system

    stats_by_it = {stat["n1"].it: stat for stat in liste_ru_stats}
    n1_directs_stats = [stats_by_it[c.it] for c in n1_directs if c.it in stats_by_it]

    n2_groups = []
    for n2 in n2_list:
        sous_managers = n2_to_n1.get(n2.it, [])
        sous_operateurs = n2_to_operateurs.get(n2.it, [])
        n1_stats_n2 = [stats_by_it[c.it] for c in sous_managers if c.it in stats_by_it]
        n2_groups.append({
            "n2": n2, "n1_stats": n1_stats_n2,
            "directs": {"liste": sous_operateurs, "reel": len(sous_operateurs)},
            "nbr_collabs_total": sum(s["reel"] for s in n1_stats_n2) + len(sous_operateurs),
        })

    lot_stats = {}
    lot_details = {}

    def _cle_lot(lot_value):
        if lot_value in ("A", "O"):
            return "A/O"
        return lot_value or "Non défini"

    def add_to_lot(lot_value, reel=0, systeme=0, maquette=0):
        lot_key = _cle_lot(lot_value)
        lot_stats.setdefault(lot_key, {"reel": 0, "systeme": 0, "maquette": 0})
        lot_stats[lot_key]["reel"] += reel
        lot_stats[lot_key]["systeme"] += systeme
        lot_stats[lot_key]["maquette"] += maquette

    def add_detail(lot_value, nom, reel=0, systeme=0, maquette=0):
        lot_key = _cle_lot(lot_value)
        lot_details.setdefault(lot_key, []).append({"nom": nom, "reel": reel, "systeme": systeme, "maquette": maquette})

    # ---- OPTIMISATION : on réutilise systeme_lot / reel_lot calculés
    # plus haut, au lieu de rappeler SystEff()/reelEff() une seconde fois
    # par N1 rien que pour la répartition par lot. ----
    for stat in liste_ru_stats:
        n1 = stat["n1"]
        n1_it = n1.it

        systeme_par_lot_n1 = systeme_lot.get(n1_it, {})
        reel_map_n1 = reel_lot.get(n1_it, {})
        lots_vus_systeme = set(systeme_par_lot_n1.keys())

        for lot_val, s_count in systeme_par_lot_n1.items():
            r_count = reel_map_n1.get(lot_val, 0)
            add_to_lot(lot_val, reel=r_count, systeme=s_count)
            add_detail(lot_val, f"{n1.nom_complete} (équipe)", reel=r_count, systeme=s_count)

        for lot_val, r_count in reel_map_n1.items():
            if lot_val not in lots_vus_systeme:
                add_to_lot(lot_val, reel=r_count)
                add_detail(lot_val, f"{n1.nom_complete} (équipe)", reel=r_count)

        add_to_lot(getattr(n1, "lot", None), reel=1, systeme=1)
        add_detail(getattr(n1, "lot", None), getattr(n1, "nom_complete", n1_it), reel=1, systeme=1)

    for op in operateurs_directs_n3:
        add_to_lot(getattr(op, "lot", None), reel=1, systeme=1)
        add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)

    for sous_operateurs in n2_to_operateurs.values():
        for op in sous_operateurs:
            add_to_lot(getattr(op, "lot", None), reel=1, systeme=1)
            add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)

    for n2 in n2_list:
        add_to_lot(getattr(n2, "lot", None), reel=1, systeme=1)
        add_detail(getattr(n2, "lot", None), getattr(n2, "nom_complete", n2.it), reel=1, systeme=1)

    # ---- FIX : ce bloc était auparavant IMBRIQUÉ dans la boucle
    # "for lot_key in (...)" ci-dessous (bug d'indentation), ce qui
    # exécutait ces requêtes 4 FOIS pour rien. Il est maintenant sorti
    # de la boucle et exécuté une seule fois. ----
    for lot_key in ("A/O", "P", "E", "C"):
        lot_stats.setdefault(lot_key, {"reel": 0, "systeme": 0, "maquette": 0})

    # ---- Départs déclarés mais toujours présents dans la table Collaborateur ----
    declarations_departs = list(
        declaration_effectif.objects.filter(nature='D', Ru_id__in=tous_les_ru_ids).order_by('-date')
    )
    ids_collabs_departs = {d.collaborateur_it_id for d in declarations_departs}
    collabs_departs_map = {
        c.it: c for c in Collaborateur.objects.filter(it__in=ids_collabs_departs)
    }

    liste_departs = [
        {
            "it": d.collaborateur_it_id,
            "nom": collabs_departs_map[d.collaborateur_it_id].nom_complete,
            "lot": collabs_departs_map[d.collaborateur_it_id].lot,
            "date_declaration": d.date,
            "ru": d.Ru_id,
        }
        for d in declarations_departs
        if d.collaborateur_it_id in collabs_departs_map
    ]

    # ---- Changements déclarés mais pas encore appliqués dans la table Collaborateur ----
    declarations_changements = list(
        declaration_effectif.objects.filter(nature='C', Ru_id__in=tous_les_ru_ids).order_by('-date')
    )
    ids_collabs_changements = {c.collaborateur_it_id for c in declarations_changements}
    collabs_changements_map = {
        c.it: c for c in Collaborateur.objects.filter(it__in=ids_collabs_changements)
    }

    liste_changements_non_faits = []
    for c in declarations_changements:
        collab = collabs_changements_map.get(c.collaborateur_it_id)
        if not collab:
            continue

        diffs = {}
        nouveau_ru = c.nv_Ru_id
        if nouveau_ru is not None and str(nouveau_ru) != str(collab.ru_it_id):
            diffs['ru_it'] = {"ancien": collab.ru_it_id, "nouveau": nouveau_ru}

        if diffs:
            liste_changements_non_faits.append({
                "it": c.collaborateur_it_id,
                "nom": collab.nom_complete,
                "date_declaration": c.date,
                "ru": c.Ru_id,
                "diffs": diffs,
            })

    maquette_n3_obj = MaquetteN1.objects.filter(n1_id=it_session_original, actif=True).first()
    maquette_totale = maquette_n3_obj.total if maquette_n3_obj else 0
    stats = MaquetteN1.objects.filter(
        n1_id=it_session_original,
        actif=True
    ).aggregate(
        somme_ap=Sum(F('A') + F('P')),
        somme_ce=Sum(F('C') + F('T'))
    )

    somme_ap = stats['somme_ap'] or 0
    somme_ce = stats['somme_ce'] or 0
    # Répartition par lot cohérente avec maquette_totale : on utilise
    # UNIQUEMENT la ligne propre du N+3, pas celles de ses N+1/N+2.
    maquette_map_pour_lot = {it_session_original: maquette_n3_obj} if maquette_n3_obj else {}
    repartir_maquette_par_lot(maquette_map_pour_lot, lot_stats)

    lot_labels = list(lot_stats.keys())
    lot_reel_data = [lot_stats[l]["reel"] for l in lot_labels]
    lot_systeme_data = [lot_stats[l]["systeme"] for l in lot_labels]
    lot_maquette_data = [lot_stats[l]["maquette"] for l in lot_labels]

    labels_ru_liste = [s["n1"].nom_complete for s in liste_ru_stats]
    data_reel_liste = [s["reel"] for s in liste_ru_stats]
    data_systeme_liste = [s["systeme"] for s in liste_ru_stats]
    data_maquette_liste = [s["maquette"] for s in liste_ru_stats]

    context = {
        "liste_ru_stats": liste_ru_stats,
        "total_r": total_r, "total_syst": total_syst,
        "MR": total_r - maquette_totale, "MS": total_syst - maquette_totale,
        "maquette_totale": maquette_totale, "maint": maint_global,
        "non_valides": non_valides,
        "operateurs_systeme_session": operateurs_systeme_session,
        "syste_session": syste_session,
        "directs_n3": {"liste": operateurs_directs_n3, "reel": len(operateurs_directs_n3)},
        "operateurs_directs_n3": len(operateurs_directs_n3),
        "n1_directs": n1_directs, "n1_directs_stats": n1_directs_stats,
        "n2_list": Collaborateur.objects.filter(it__in=it_n2),
        "nbr_n1_total": nbr_n1_total, "nbr_n2_total": nbr_n2_total,
        "chart_labels": labels_list, "chart_data": data_totale_par_jour,
        "labels_ru": labels_ru_liste, "data_reel": data_reel_liste,
        "data_systeme": data_systeme_liste, "data_maquette": data_maquette_liste,
        "lot_labels": lot_labels, "lot_reel": lot_reel_data,
        "lot_systeme": lot_systeme_data, "lot_maquette": lot_maquette_data,
        "lot_details": lot_details,"liste_departs":liste_departs,"liste_changements_non_faits":liste_changements_non_faits,
        "nbr_departs": len(liste_departs),"nbr_changements_non_faits": len(liste_changements_non_faits),"somme_ap":somme_ap,"somme_ce":somme_ce
    }
    return render(request, "declaration_effectif/N3/dashboard.html", context)


def calculer_evolution_reel_mensuelle(ru_ids, systeme_par_ru, part_fixe, nb_annees=3):
    ru_ids = list(ru_ids)
    today = timezone.now().date()
    annee_courante = today.year
    annees = list(range(annee_courante - nb_annees + 1, annee_courante + 1))

    if not ru_ids:
        mois_cles, mois_labels = [], []
        for annee in annees:
            mois_max = today.month if annee == annee_courante else 12
            for m in range(1, mois_max + 1):
                mois_cles.append((annee, m))
                mois_labels.append(f"{m:02d}/{annee}")
        data = [part_fixe] * len(mois_cles)
        labels_par_annee, data_par_annee = {}, {}
        for (annee, mois), v in zip(mois_cles, data):
            labels_par_annee.setdefault(annee, []).append(f"{mois:02d}")
            data_par_annee.setdefault(annee, []).append(v)
        return {"labels": mois_labels, "data": data, "labels_par_annee": labels_par_annee,
                "data_par_annee": data_par_annee, "annees": annees}

    declarations = (
        declaration_effectif.objects
        .filter(Ru_id__in=ru_ids, nature__in=["A", "V"])
        .values('Ru_id', 'date')
        .annotate(total=Count('collaborateur_it_id', distinct=True))
        .order_by('Ru_id', 'date')
    )

    par_ru = {}
    for r in declarations:
        cle_mois = (r['date'].year, r['date'].month)
        par_ru.setdefault(r['Ru_id'], {})[cle_mois] = r['total']

    cles_triees_par_ru = {ru_id: sorted(mois_dict.keys()) for ru_id, mois_dict in par_ru.items()}

    mois_cles, mois_labels = [], []
    for annee in annees:
        mois_max = today.month if annee == annee_courante else 12
        for m in range(1, mois_max + 1):
            mois_cles.append((annee, m))
            mois_labels.append(f"{m:02d}/{annee}")

    data_totale_par_mois = [0] * len(mois_cles)
    for ru_id in ru_ids:
        mois_ru = par_ru.get(ru_id, {})
        cles_ru = cles_triees_par_ru.get(ru_id, [])
        fallback = systeme_par_ru.get(ru_id, 0)
        for idx, cle in enumerate(mois_cles):
            pos = bisect.bisect_right(cles_ru, cle) - 1
            valeur = mois_ru[cles_ru[pos]] if pos >= 0 else fallback
            data_totale_par_mois[idx] += valeur

    for idx in range(len(mois_cles)):
        data_totale_par_mois[idx] += part_fixe

    labels_par_annee, data_par_annee = {}, {}
    for (annee, mois), valeur in zip(mois_cles, data_totale_par_mois):
        labels_par_annee.setdefault(annee, []).append(f"{mois:02d}")
        data_par_annee.setdefault(annee, []).append(valeur)

    return {"labels": mois_labels, "data": data_totale_par_mois,
            "labels_par_annee": labels_par_annee, "data_par_annee": data_par_annee, "annees": annees}



@role_required('N+4')
def page_N4(request):
    it_session_original = request.session.get("it")
    if not it_session_original:
        return redirect("login")

    tous_les_it = get_tous_les_it_sous(it_session_original)
    niveau_par_it, l1, l2, l3, l4 = calculer_niveaux_hierarchie()
    tous_managers_classes = l1 | l2 | l3 | l4
    tous_ru_it = get_managers_its()

    managers_ids = tous_les_it & tous_managers_classes
    operateurs_ids = tous_les_it - tous_managers_classes

    vrais_n1_ids = {m for m in managers_ids if niveau_par_it.get(m) == 1}

    # ---- Départs déclarés mais toujours présents dans la table Collaborateur ----
    declarations_departs = list(
        declaration_effectif.objects.filter(nature='D', Ru_id__in=vrais_n1_ids).order_by('-date')
    )
    ids_collabs_departs = {d.collaborateur_it_id for d in declarations_departs}
    collabs_departs_map = {
        c.it: c for c in Collaborateur.objects.filter(it__in=ids_collabs_departs)
    }

    liste_departs = [
        {
            "it": d.collaborateur_it_id,
            "nom": collabs_departs_map[d.collaborateur_it_id].nom_complete,
            "lot": collabs_departs_map[d.collaborateur_it_id].lot,
            "date_declaration": d.date,
            "ru": d.Ru_id,
        }
        for d in declarations_departs
        if d.collaborateur_it_id in collabs_departs_map
    ]

    # ---- Changements déclarés mais pas encore appliqués dans la table Collaborateur ----
    declarations_changements = list(
        declaration_effectif.objects.filter(nature='C', Ru_id__in=vrais_n1_ids).order_by('-date')
    )
    ids_collabs_changements = {c.collaborateur_it_id for c in declarations_changements}
    collabs_changements_map = {
        c.it: c for c in Collaborateur.objects.filter(it__in=ids_collabs_changements)
    }

    liste_changements_non_faits = []
    for c in declarations_changements:
        collab = collabs_changements_map.get(c.collaborateur_it_id)
        if not collab:
            continue

        diffs = {}
        nouveau_ru = c.nv_Ru_id
        if nouveau_ru is not None and str(nouveau_ru) != str(collab.ru_it_id):
            diffs['ru_it'] = {"ancien": collab.ru_it_id, "nouveau": nouveau_ru}

        if diffs:
            liste_changements_non_faits.append({
                "it": c.collaborateur_it_id,
                "nom": collab.nom_complete,
                "date_declaration": c.date,
                "ru": c.Ru_id,
                "diffs": diffs,
            })

    nbr_n1_total = len(vrais_n1_ids)
    nbr_n2_total = sum(1 for m in managers_ids if niveau_par_it.get(m) == 2)
    nbr_n3_total = sum(1 for m in managers_ids if niveau_par_it.get(m, 0) >= 3)
    n2_ids = {m for m in managers_ids if niveau_par_it.get(m) == 2}
    n3_ids = {m for m in managers_ids if niveau_par_it.get(m, 0) >= 3}

    liste_n2_totale = Collaborateur.objects.filter(it__in=n2_ids)
    liste_n3_totale = Collaborateur.objects.filter(it__in=n3_ids)
    directs_n4 = list(
        Collaborateur.objects.filter(ru_it_id=it_session_original)
        .exclude(it=it_session_original)
        .select_related("departement")
    )
    operateurs_directs_n4 = [
        c for c in directs_n4 if c.it not in tous_managers_classes and c.lot in ("A", "P")
    ]
    n1_directs_n4_collabs = [c for c in directs_n4 if niveau_par_it.get(c.it) == 1]
    n2_directs_n4 = [c for c in directs_n4 if niveau_par_it.get(c.it) == 2]
    n3_directs_n4 = [c for c in directs_n4 if niveau_par_it.get(c.it, 0) >= 3]

    if not vrais_n1_ids and not operateurs_ids:
        annee_courante = str(timezone.now().year)
        return render(request, "declaration_effectif/N4/dashboard.html", {
            "liste_ru_stats": [], "reel": 0, "systeme": 0, "maquette": 0, "MR": 0, "MS": 0,
            "maint": timezone.localdate(), "non_valides": 0,
            "chart_labels_json": json.dumps([]), "chart_data_json": json.dumps([]),
            "operateurs_directs_n4": {"liste": [], "reel": 0},
            "n1_directs_n4": [], "n2_groups": [], "n3_groups": [],
            "nbr_n1_total": 0, "nbr_n2_total": 0, "nbr_n3_total": 0,
            "lot_labels": [], "lot_reel": [], "lot_systeme": [], "lot_maquette": [], "lot_details": {},
            "evolution_mensuelle_labels_json": json.dumps([]),
            "evolution_mensuelle_data_json": json.dumps([]),
            "evolution_mensuelle_labels_par_annee_json": json.dumps({annee_courante: []}),
            "evolution_mensuelle_data_par_annee_json": json.dumps({annee_courante: []}),
            "evolution_mensuelle_annees": [annee_courante],
            "liste_departs": [], "liste_changements_non_faits": [],
            "nbr_departs": 0, "nbr_changements_non_faits": 0,
            "liste_n2_totale": [], "liste_n3_totale": [],
            "somme_ap": 0, "somme_ce": 0,
        })

    today = timezone.now().date()
    start = today.replace(day=1)
    nb_jours = (today - start).days + 1
    dates = [start + timedelta(days=i) for i in range(nb_jours)]
    labels_list = [d.strftime("%d %b") for d in dates]

    liste_n1 = Collaborateur.objects.filter(it__in=vrais_n1_ids).select_related("departement")
    maquettes_n1_map = get_maquettes_n1_map(vrais_n1_ids)

    # ---- OPTIMISATION : systeme/reel (total ET par lot) calculés EN BLOC
    # pour tous les N1 d'un coup, réutilisés dans les DEUX boucles
    # ci-dessous. ----
    vrais_n1_liste = [c.it for c in liste_n1]
    systeme_total, systeme_lot = _systeme_effectif_batch(vrais_n1_liste, tous_ru_it)
    reel_total, reel_lot = _reel_effectif_lot_batch(vrais_n1_liste)
    dernieres_dates_av = dict(
        declaration_effectif.objects.filter(Ru_id__in=vrais_n1_liste, nature__in=["A", "V"])
        .values('Ru_id').annotate(d=Max('date')).values_list('Ru_id', 'd')
    )

    stats_by_it = {}
    maint_global = None
    systeme_par_n1 = {}

    for collab in liste_n1:
        systeme = systeme_total.get(collab.it, 0)
        reel = reel_total.get(collab.it, 0)
        systeme_par_n1[collab.it] = systeme

        date_ref = dernieres_dates_av.get(collab.it)

        maquette_obj = maquettes_n1_map.get(collab.it)
        maquette_brute = maquette_obj.total if maquette_obj else 0

        stats_by_it[collab.it] = {
            "n1": collab,
            "matricule": getattr(collab, "matricule", None),
            "nom_complete": getattr(collab, "nom_complete", ""),
            "eq": getattr(collab, "eq_id", None),
            "reel1": reel, "systeme1": systeme, "maquette1": maquette_brute,
            "mr": reel - maquette_brute, "ms": systeme - maquette_brute,
            "last_decl_date": date_ref,
        }
        if date_ref and (maint_global is None or date_ref > maint_global):
            maint_global = date_ref

    if maint_global is None:
        maint_global = timezone.localdate()

    liste_ru_stats = list(stats_by_it.values())
    
    operateurs_sous_vrais_n1 = set(
        Collaborateur.objects.filter(ru_it_id__in=vrais_n1_ids).values_list("it", flat=True)
    )
    operateurs_intermediaires_ids = operateurs_ids - operateurs_sous_vrais_n1

    maquette_n4_obj = MaquetteN1.objects.filter(n1_id=it_session_original, actif=True).first()
    maquette = maquette_n4_obj.total if maquette_n4_obj else 0

    reel = (
        sum(s["reel1"] for s in liste_ru_stats)
        + len(operateurs_intermediaires_ids) + nbr_n1_total + nbr_n2_total + len(n3_directs_n4)
    )
    systeme = (
        sum(s["systeme1"] for s in liste_ru_stats)
        +len(operateurs_intermediaires_ids) + nbr_n1_total + nbr_n2_total + len(n3_directs_n4)
    )

    stats = MaquetteN1.objects.filter(
        n1_id=it_session_original,
        actif=True
    ).aggregate(
        somme_ap=Sum(F('A') + F('P')),
        somme_ce=Sum(F('C') + F('T'))
    )

    somme_ap = stats['somme_ap'] or 0
    somme_ce = stats['somme_ce'] or 0

    liste_declares_today = set(
        declaration_effectif.objects.filter(date=today, Ru_id__in=vrais_n1_ids).values_list("Ru_id", flat=True)
    )
    non_valides = sum(
        1 for s in liste_ru_stats if s["systeme1"] > 0 and s["n1"].it not in liste_declares_today
    )

    decl_window_qs = (
        declaration_effectif.objects
        .filter(Ru_id__in=vrais_n1_ids, nature__in=["A", "V"], date__gte=start, date__lte=today)
        .values("Ru_id", "date")
        .annotate(total=Count("collaborateur_it_id", distinct=True))
    )
    decls_by_ru = {}
    for r in decl_window_qs:
        decls_by_ru.setdefault(r["Ru_id"], []).append((r["date"], r["total"]))
    for k in decls_by_ru:
        decls_by_ru[k].sort()

    part_fixe_par_jour = len(operateurs_intermediaires_ids) + len(managers_ids)
    data_totale_par_jour = [part_fixe_par_jour] * len(dates)
    systeme_par_n1_pour_bisect = {s["n1"].it: s["systeme1"] for s in liste_ru_stats}

    for ru_it_key in vrais_n1_ids:
        ru_system = systeme_par_n1_pour_bisect.get(ru_it_key, 0)
        ru_decls = decls_by_ru.get(ru_it_key, [])
        ru_dates = [dt for dt, _ in ru_decls]
        ru_totals = [t for _, t in ru_decls]
        for idx, d in enumerate(dates):
            if ru_dates:
                pos = bisect.bisect_right(ru_dates, d) - 1
                if pos >= 0:
                    data_totale_par_jour[idx] += ru_totals[pos]
                    continue
            data_totale_par_jour[idx] += ru_system

    annee_actuelle = str(today.year)
    evolution_mensuelle_raw = calculer_evolution_reel_mensuelle(
        ru_ids=vrais_n1_ids, systeme_par_ru=systeme_par_n1_pour_bisect,
        part_fixe=part_fixe_par_jour, nb_annees=1,
    )
    labels_annee = evolution_mensuelle_raw.get("labels_par_annee", {}).get(
        annee_actuelle, evolution_mensuelle_raw.get("labels", [])
    )
    data_annee = evolution_mensuelle_raw.get("data_par_annee", {}).get(
        annee_actuelle, evolution_mensuelle_raw.get("data", [])
    )
    labels_par_annee_json = json.dumps({annee_actuelle: labels_annee})
    data_par_annee_json = json.dumps({annee_actuelle: data_annee})
    annees_liste = [annee_actuelle]

    def operateurs_directs_de(m_it):
        return list(Collaborateur.objects.filter(ru_it_id=m_it, it__in=operateurs_intermediaires_ids))

    def sous_managers_de(m_it, niveau_cible):
        return [
            c for c in Collaborateur.objects.filter(ru_it_id=m_it, it__in=managers_ids)
            if niveau_par_it.get(c.it) == niveau_cible
        ]

    def sous_managers_niveau_min(m_it, niveau_min):
        return [
            c for c in Collaborateur.objects.filter(ru_it_id=m_it, it__in=managers_ids)
            if niveau_par_it.get(c.it, 0) >= niveau_min
        ]

    def construire_n2_group(n2):
        sous_n1 = sous_managers_de(n2.it, 1)
        n1_stats = [stats_by_it[c.it] for c in sous_n1 if c.it in stats_by_it]
        directs_ops = operateurs_directs_de(n2.it)
        nbr_total = sum(s["reel1"] for s in n1_stats) + len(n1_stats) + len(directs_ops)
        return {"n2": n2, "n1_stats": n1_stats,
                "directs": {"liste": directs_ops, "reel": len(directs_ops)},
                "nbr_collabs_total": nbr_total}

    def construire_n3_group(n3):
        sous_n3_imbriques = sous_managers_niveau_min(n3.it, 3)
        sous_n2 = sous_managers_de(n3.it, 2)
        sous_n1_directs = sous_managers_de(n3.it, 1)

        n1_stats_directs = [stats_by_it[c.it] for c in sous_n1_directs if c.it in stats_by_it]
        n2_groups = [construire_n2_group(n2) for n2 in sous_n2]
        n3_groups_imbriques = [construire_n3_group(n3b) for n3b in sous_n3_imbriques]
        directs_ops = operateurs_directs_de(n3.it)

        nbr_total = (
            sum(g["nbr_collabs_total"] for g in n2_groups) + len(n2_groups)
            + sum(g["nbr_collabs_total"] for g in n3_groups_imbriques) + len(n3_groups_imbriques)
            + sum(s["reel1"] for s in n1_stats_directs) + len(n1_stats_directs) + len(directs_ops)
        )
        return {"n3": n3, "n1_stats_directs": n1_stats_directs, "n2_groups": n2_groups,
                "n3_groups_imbriques": n3_groups_imbriques,
                "directs": {"liste": directs_ops, "reel": len(directs_ops)},
                "nbr_collabs_total": nbr_total}

    n3_groups = [construire_n3_group(n3) for n3 in n3_directs_n4]
    n2_groups_directs_n4 = [construire_n2_group(n2) for n2 in n2_directs_n4]
    n1_stats_directs_n4 = [stats_by_it[c.it] for c in n1_directs_n4_collabs if c.it in stats_by_it]

    # ---- CALCUL STATS PAR LOT ----
    lot_stats = {}
    lot_details = {}

    def _cle_lot(lot_value):
        if lot_value in ("A", "O", "A/O"):
            return "A/O"
        return lot_value or "Non défini"

    def add_to_lot(lot_value, reel=0, systeme=0, maquette=0):
        lot_key = _cle_lot(lot_value)
        lot_stats.setdefault(lot_key, {"reel": 0, "systeme": 0, "maquette": 0})
        lot_stats[lot_key]["reel"] += reel
        lot_stats[lot_key]["systeme"] += systeme
        lot_stats[lot_key]["maquette"] += maquette

    def add_detail(lot_value, nom, reel=0, systeme=0, maquette=0):
        lot_key = _cle_lot(lot_value)
        lot_details.setdefault(lot_key, []).append({"nom": nom, "reel": reel, "systeme": systeme, "maquette": maquette})

    # ---- OPTIMISATION : on réutilise systeme_lot / reel_lot calculés
    # plus haut, au lieu de rappeler SystEff()/reelEff() une seconde fois
    # par N1 rien que pour la répartition par lot. ----
    for collab in liste_n1:
        it_n1 = collab.it
        systeme_par_lot_n1 = systeme_lot.get(it_n1, {})
        reel_map_n1 = reel_lot.get(it_n1, {})
        lots_vus_systeme = set(systeme_par_lot_n1.keys())

        for lot_val, s_count in systeme_par_lot_n1.items():
            r_count = reel_map_n1.get(lot_val, 0)
            add_to_lot(lot_val, reel=r_count, systeme=s_count)
            add_detail(lot_val, f"{getattr(collab, 'nom_complete', it_n1)} (équipe)", reel=r_count, systeme=s_count)

        for lot_val, r_count in reel_map_n1.items():
            if lot_val not in lots_vus_systeme:
                add_to_lot(lot_val, reel=r_count)
                add_detail(lot_val, f"{getattr(collab, 'nom_complete', it_n1)} (équipe)", reel=r_count)

        add_to_lot(getattr(collab, "lot", None), reel=1, systeme=1)
        add_detail(getattr(collab, "lot", None), getattr(collab, "nom_complete", it_n1), reel=1, systeme=1)

    for op in operateurs_directs_n4:
        add_to_lot(getattr(op, "lot", None), reel=1, systeme=1)
        add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)

    ids_deja_comptes = {o.it for o in operateurs_directs_n4}
    operateurs_intermediaires_hors_n4 = Collaborateur.objects.filter(
        it__in=operateurs_intermediaires_ids
    ).exclude(it__in=ids_deja_comptes)

    for op in operateurs_intermediaires_hors_n4:
        add_to_lot(getattr(op, "lot", None), reel=1, systeme=1)
        add_detail(getattr(op, "lot", None), getattr(op, "nom_complete", op.it), reel=1, systeme=1)

    managers_n2_n3 = Collaborateur.objects.filter(it__in=(managers_ids - vrais_n1_ids))
    for m in managers_n2_n3:
        add_to_lot(getattr(m, "lot", None), reel=1, systeme=1)
        add_detail(getattr(m, "lot", None), getattr(m, "nom_complete", m.it), reel=1, systeme=1)

    for lot_key in ("A/O", "P", "E", "C"):
        lot_stats.setdefault(lot_key, {"reel": 0, "systeme": 0, "maquette": 0})

    maquette_map_pour_lot = {it_session_original: maquette_n4_obj} if maquette_n4_obj else {}
    repartir_maquette_par_lot(maquette_map_pour_lot, lot_stats)

    for k in ("A", "O"):
        if k in lot_stats:
            lot_stats["A/O"]["maquette"] += lot_stats[k].get("maquette", 0)
            del lot_stats[k]

    lot_labels = [l for l in ["A/O", "P", "E", "C"] if l in lot_stats] + [l for l in lot_stats if l not in ("A/O", "P", "E", "C")]
    lot_reel_data = [lot_stats[l]["reel"] for l in lot_labels]
    lot_systeme_data = [lot_stats[l]["systeme"] for l in lot_labels]
    lot_maquette_data = [lot_stats[l]["maquette"] for l in lot_labels]

    return render(request, "declaration_effectif/N4/dashboard.html", {
        "liste_ru_stats": liste_ru_stats,
        "reel": reel, "systeme": systeme, "maquette": maquette,
        "MR": reel - maquette, "MS": systeme - maquette,
        "maint": maint_global, "non_valides": non_valides,
        "chart_labels_json": json.dumps(labels_list),
        "chart_data_json": json.dumps(data_totale_par_jour),
        "operateurs_directs_n4": {"liste": operateurs_directs_n4, "reel": len(operateurs_directs_n4)},
        "nbr_n1_total": nbr_n1_total, "nbr_n2_total": nbr_n2_total, "nbr_n3_total": nbr_n3_total,
        "lot_labels": lot_labels, "lot_reel": lot_reel_data,
        "lot_systeme": lot_systeme_data, "lot_maquette": lot_maquette_data,
        "lot_details": lot_details,
        "n1_directs_n4": n1_stats_directs_n4,
        "n2_groups": n2_groups_directs_n4,
        "n3_groups": n3_groups,
        "evolution_mensuelle_labels_json": json.dumps(labels_annee),
        "evolution_mensuelle_data_json": json.dumps(data_annee),
        "evolution_mensuelle_labels_par_annee_json": labels_par_annee_json,
        "evolution_mensuelle_data_par_annee_json": data_par_annee_json,
        "evolution_mensuelle_annees": annees_liste,
        "liste_departs": liste_departs,
        "liste_changements_non_faits": liste_changements_non_faits,
        "nbr_departs": len(liste_departs),
        "nbr_changements_non_faits": len(liste_changements_non_faits),
        "liste_n2_totale": liste_n2_totale,
        "liste_n3_totale": liste_n3_totale,
        "somme_ap": somme_ap,
        "somme_ce": somme_ce
    })

@role_required('N+4')
def affectation_N4(request):
    util = request.session.get("it")
    if not util:
        return redirect("login")

    status = request.GET.get("status", "all")
    ru_init = request.GET.get("ru_init", "").strip()
    ru_acceuil = request.GET.get("ru_acceuil", "").strip()
    tab_actif = request.GET.get("tab", "tab-mes")

    try:
        user = Collaborateur.objects.get(it=util)
    except Collaborateur.DoesNotExist:
        return redirect("login")

    liste_N3 = liste_N3_N4(util)
    tous_sous_n4 = list(liste_N3)
    for n3 in liste_N3:
        tous_sous_n4.extend(Rg_Dur(n3))
    tous_sous_n4.extend(liste_N1_pr_N3(util))

    toutes_declarations_N4 = historique.objects.filter(
        Q(initial=user.nom_complete) | Q(acceuil=user.nom_complete)
    ).exclude(etat="Terminé")
    toutes_declarations = list(historique_pour(tous_sous_n4))

    ensemble_onglet = toutes_declarations if tab_actif == "tab-toutes" else toutes_declarations_N4
    ru_initiaux = sorted({d.initial for d in ensemble_onglet if d.initial})
    ru_acceuils = sorted({d.acceuil for d in ensemble_onglet if d.acceuil})

    def _filtrer(liste, mot_cle):
        return [d for d in liste if d.etat and mot_cle in str(d.etat).lower()]

    if status == "valide":
        toutes_declarations_N4 = _filtrer(toutes_declarations_N4, "valid")
        toutes_declarations = _filtrer(toutes_declarations, "valid")
    elif status == "refuse":
        toutes_declarations_N4 = _filtrer(toutes_declarations_N4, "refus")
        toutes_declarations = _filtrer(toutes_declarations, "refus")
    elif status == "non_demarrer":
        toutes_declarations_N4 = _filtrer(toutes_declarations_N4, "non démarr")
        toutes_declarations = _filtrer(toutes_declarations, "non démarr")

    if ru_init:
        toutes_declarations_N4 = [d for d in toutes_declarations_N4 if d.initial == ru_init]
        toutes_declarations = [d for d in toutes_declarations if d.initial == ru_init]
    if ru_acceuil:
        toutes_declarations_N4 = [d for d in toutes_declarations_N4 if d.acceuil == ru_acceuil]
        toutes_declarations = [d for d in toutes_declarations if d.acceuil == ru_acceuil]

    paginator_n4 = Paginator(toutes_declarations_N4, 10)
    page_obj_n4 = paginator_n4.get_page(request.GET.get("page_n4", 1))
    paginator_n1 = Paginator(toutes_declarations, 10)
    page_obj_n1 = paginator_n1.get_page(request.GET.get("page_n1", 1))

    return render(request, "declaration_effectif/N2/affectation.html", {
        "n2": page_obj_n4, "page_obj_n2": page_obj_n4,
        "info": page_obj_n1, "page_obj_n1": page_obj_n1,
        "nbr2": len(toutes_declarations_N4), "nbr": len(toutes_declarations),
        "status": status, "ru_init": ru_init, "ru_acceuil": ru_acceuil,
        "ru_initiaux": ru_initiaux, "ru_acceuils": ru_acceuils,
    })


@role_required('N+4')
def validation_N4(request):
    util = request.session.get("it")
    if not util:
        return redirect("login")

    maint = timezone.localdate()
    n1_its = get_tous_les_n1(util)

    liste_declares = set(
        declaration_effectif.objects.filter(date=maint, Ru_id__in=n1_its).values_list("Ru_id", flat=True)
    )
    non_valides = Collaborateur.objects.filter(it__in=n1_its).exclude(
        it__in=liste_declares
    ).select_related("departement").order_by("nom_complete")

    total_non_valides = non_valides.count()
    paginator = Paginator(non_valides, 20)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    return render(request, "declaration_effectif/N4/validation.html", {
        "non_valides": page_obj, "page_obj": page_obj,
        "total_non_valides": total_non_valides, "date": maint,
    })


def validation_date_N2(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    query_date = parse_date(time_str) if time_str else None
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    n1 = Ru_Rg(it)
    liste_n1 = set(n1.values_list("it", flat=True))

    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(date=query_date, Ru_id__in=liste_n1)
            .values_list("Ru_id", flat=True)
        )

    liste_it_manquants = liste_n1 - declarations_faites
    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )
    return JsonResponse({"resultats": resultats, "status": status})


def validation_date_N3(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    query_date = parse_date(time_str) if time_str else None
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    liste_n1 = get_tous_les_n1(it)
    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(date=query_date, Ru_id__in=liste_n1)
            .values_list("Ru_id", flat=True)
        )

    liste_it_manquants = liste_n1 - declarations_faites
    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )
    return JsonResponse({"resultats": resultats, "status": status})


def get_badge_class(etat):
    if not etat:
        return "badge-en-attente"
    etat_lower = str(etat).lower()
    if "valid" in etat_lower:
        return "badge-valide"
    elif "refus" in etat_lower:
        return "badge-refuse"
    elif "non démarr" in etat_lower or "non demarr" in etat_lower:
        return "badge-non-demarrer"
    return "badge-en-attente"


@role_required(['HRBP', 'ADMIN'])
def affectation_HRBP(request):
    it = request.session.get("it")
    status = request.GET.get("status", "all")
    dpt_filtre = request.GET.get("dpt", "all")
    role = request.session.get("role")

    if role == "HRBP":
        departements_qs = Departement.objects.filter(HRBP_id=it)
    elif role == "ADMIN":
        departements_qs = Departement.objects.filter(ADMIN_id=it)
    else:
        departements_qs = Departement.objects.none()

    departements = list(departements_qs.values_list("abreviation", flat=True))

    affectation = historique.objects.filter(
        Q(dpt_init__in=departements) | Q(dpt_acceuil__in=departements)
    ).exclude(etat="Terminé")

    if status == "valide":
        affectation = affectation.filter(etat__icontains="valid")
    elif status == "refuse":
        affectation = affectation.filter(etat__icontains="refus")
    elif status == "non_demarrer":
        affectation = affectation.filter(etat__icontains="non démarr")

    if dpt_filtre != "all" and dpt_filtre in departements:
        affectation = affectation.filter(
            Q(dpt_init__abreviation=dpt_filtre) | Q(dpt_acceuil__abreviation=dpt_filtre)
        )

    paginator = Paginator(affectation, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    for a in page_obj:
        a.badge_class = get_badge_class(a.etat)

    return render(request, "declaration_effectif/HRBP/affectation.html", {
        "info": page_obj, "page_obj": page_obj, "status": status,
        "dpt_filtre": dpt_filtre, "departements": departements,
    })


@never_cache
@role_required('HRBP')
def responsables_ru_sans_declaration_du_jour(request):
    today = timezone.localdate()
    it = request.session.get("it")

    departements_qs = Departement.objects.filter(HRBP_id=it)
    departements = departements_qs.values_list("abreviation", flat=True)
    collaborateurs_base = Collaborateur.objects.filter(departement_id__in=departements)
    responsable = list(
        collaborateurs_base.exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True).distinct()
    )

    operateur = Collaborateur.objects.filter(
        departement_id__in=departements, lot__in=["A", "O", "P"]
    ).exclude(it__in=responsable)

    ru_ids = list(
        operateur.exclude(ru_it_id__isnull=True).values_list("ru_it_id", flat=True).distinct()
    )
    ru_avec_declaration = set(
        declaration_effectif.objects.filter(date=today).values_list("Ru_id", flat=True).distinct()
    )
    ru_ids_sans_declaration = [ru_id for ru_id in ru_ids if ru_id not in ru_avec_declaration]
    ru = Collaborateur.objects.filter(it__in=ru_ids_sans_declaration)

    paginator = Paginator(ru, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "declaration_effectif/HRBP/declaration.html", {
        "ru": page_obj, "non_valides": ru, "page_obj": page_obj,
        "departements": departements_qs, "date": today,
    })


def filter_date2(request):
    it = request.session.get("it")
    date_str = request.GET.get("time")
    dept = request.GET.get("dept", "").strip()

    if not date_str:
        return JsonResponse({"resultats": [], "status": False})

    try:
        date_selectionnee = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"resultats": [], "status": False})

    is_today = (date_selectionnee == timezone.localdate())
    status = not is_today

    departements_qs = Departement.objects.filter(HRBP_id=it)
    departements = list(departements_qs.values_list("abreviation", flat=True))
    if dept:
        departements = [d for d in departements if d == dept]

    collaborateurs_base = Collaborateur.objects.filter(departement_id__in=departements)
    responsable = list(
        collaborateurs_base.exclude(ru_it_id__isnull=True).values_list("ru_it_id", flat=True).distinct()
    )

    operateur = Collaborateur.objects.filter(
        departement_id__in=departements, lot__in=["A", "O", "P"]
    ).exclude(it__in=responsable)

    ru_ids = list(
        operateur.exclude(ru_it_id__isnull=True).values_list("ru_it_id", flat=True).distinct()
    )
    ru_avec_declaration = set(
        declaration_effectif.objects.filter(date=date_selectionnee).values_list("Ru_id", flat=True).distinct()
    )
    ru_ids_sans_declaration = [ru_id for ru_id in ru_ids if ru_id not in ru_avec_declaration]

    ru_qs = Collaborateur.objects.filter(it__in=ru_ids_sans_declaration).select_related("departement")
    resultats = [
        {
            "matricule": r.matricule, "it": r.it, "nom_complete": r.nom_complete, "lot": r.lot,
            "departement": {"abreviation": r.departement.abreviation if r.departement else ""},
        }
        for r in ru_qs
    ]
    return JsonResponse({"resultats": resultats, "status": status})


def validation_date_N4(request):
    time_str = request.GET.get("time", "")
    it = request.session.get("it")

    query_date = parse_date(time_str) if time_str else None
    is_today = (query_date == timezone.localdate()) if query_date else False
    status = not is_today

    liste_n1 = get_tous_les_n1(it)
    declarations_faites = set()
    if query_date:
        declarations_faites = set(
            declaration_effectif.objects.filter(date=query_date, Ru_id__in=liste_n1)
            .values_list("Ru_id", flat=True)
        )

    liste_it_manquants = liste_n1 - declarations_faites
    resultats = list(
        Collaborateur.objects.filter(it__in=liste_it_manquants)
        .values("matricule", "it", "nom_complete", "lot")
    )
    return JsonResponse({"resultats": resultats, "status": status})


def changement_dpt(request):
    it = request.session.get("it")
    departement = get_object_or_404(Departement, PILOT_id=it)

    status = request.GET.get("status", "all")
    ru_init = request.GET.get("ru_init", "all")
    ru_acceuil = request.GET.get("ru_acceuil", "all")
    page_number = request.GET.get("page", 1)

    base_qs = (
        historique.objects
        .filter(Q(dpt_init=departement) | Q(dpt_acceuil=departement))
        .exclude(etat="Terminé")
    )

    ru_init_list = sorted([
        r for r in base_qs.values_list("initial", flat=True).distinct()
        if r and str(r).lower() != 'nan'
    ])
    ru_acceuil_list = sorted([
        r for r in base_qs.values_list("acceuil", flat=True).distinct()
        if r and str(r).lower() != 'nan'
    ])

    changement = base_qs.order_by("-id")

    if status == "valide":
        changement = changement.filter(etat__icontains="valid")
    elif status == "refuse":
        changement = changement.filter(etat__icontains="refus")
    elif status == "non_demarrer":
        changement = changement.filter(etat__icontains="non démarr")

    if ru_init and ru_init != "all":
        changement = changement.filter(initial=ru_init)
    if ru_acceuil and ru_acceuil != "all":
        changement = changement.filter(acceuil=ru_acceuil)

    changement_list = list(changement)
    for item in changement_list:
        etat_str = str(item.etat).lower() if item.etat else ""
        if "valid" in etat_str:
            item.custom_badge = "badge-valide"
        elif "refus" in etat_str:
            item.custom_badge = "badge-refuse"
        elif "non" in etat_str or "démarr" in etat_str:
            item.custom_badge = "badge-non_demarrer"
        else:
            item.custom_badge = "badge-en-attente"

    nbr = len(changement_list)
    paginator = Paginator(changement_list, 10)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages if paginator.num_pages else 1)

    return render(request, "declaration_effectif/PILOT/affectation.html", {
        "info": page_obj, "page_obj": page_obj, "nbr": nbr, "status": status,
        "ru_init_selected": ru_init, "ru_acceuil_selected": ru_acceuil,
        "ru_init_list": ru_init_list, "ru_acceuil_list": ru_acceuil_list,
    })