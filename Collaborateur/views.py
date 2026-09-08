from itertools import count
from utilisateur.decorators import role_required
from django.shortcuts import render, redirect , get_object_or_404
from utilisateur.models import utilisateur
from Collaborateur.models import Departement , Collaborateur
from django.contrib.auth.hashers import make_password , check_password
from django.db.models import Q , Exists, OuterRef, Subquery , Count ,Max ,F
from django.contrib import messages
from django.utils import timezone
from declaration_effectif.models import declaration_effectif
from django.http import JsonResponse, request, Http404
from datetime import date , datetime
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.cache import cache
from import_data.views import get_collaborateurs_reels

# ============================================================
# CACHE — hiérarchie & managers
# ============================================================
# Ces caches évitent de refaire un scan complet de la table
# Collaborateur (ou de declaration_effectif) à chaque appel de
# SystEff/reelEff/get_tous_les_it_sous/get_tous_les_n1, qui sont
# appelées en boucle dans les dashboards N+2/N+3/N+4 et à chaque
# frappe clavier dans les recherches AJAX.

CACHE_TTL = 120  # secondes


def get_managers_its():
    """
    Ensemble des 'it' qui sont responsables d'au moins un collaborateur.
    Calculé UNE fois (1 requête) puis mis en cache, au lieu d'être
    recalculé à chaque appel de SystEff/reelEff.
    """
    managers = cache.get("managers_its")
    if managers is None:
        managers = set(
            Collaborateur.objects.exclude(ru_it_id__isnull=True)
            .values_list("ru_it_id", flat=True)
            .distinct()
        )
        cache.set("managers_its", managers, CACHE_TTL)
    return managers


def get_hierarchie_map():
    """
    Construit, en UNE seule requête, la map {ru_it_id: [it_enfants]}
    pour toute la table Collaborateur. Permet ensuite de parcourir
    n'importe quelle sous-hiérarchie EN MÉMOIRE (0 requête), au lieu
    de faire une requête par nœud comme le faisait l'ancienne version
    récursive de get_tous_les_it_sous / get_tous_les_n1.
    """
    mapping = cache.get("hierarchie_map")
    if mapping is None:
        mapping = {}
        paires = Collaborateur.objects.exclude(ru_it_id__isnull=True).values_list("it", "ru_it_id")
        for it_c, ru_it_id in paires:
            if ru_it_id != it_c:
                mapping.setdefault(ru_it_id, []).append(it_c)
        cache.set("hierarchie_map", mapping, CACHE_TTL)
    return mapping


def invalider_cache_hierarchie():
    """
    À appeler après toute création/modification de déclaration ou de
    rattachement (ru_it), pour que les caches ci-dessus reflètent le
    nouvel état. Appelé notamment depuis declaration_effectif.views.valider().
    """
    cache.delete_many(["managers_its", "hierarchie_map", "ru_reel_et_departs"])


#-------------------------------------------------------#
#Cette fct return les operateurs d'un responsable N+1
#-------------------------------------------------------#
def rec(request):
    it = request.session.get("it")
    der = (
        declaration_effectif.objects
        .filter(Q(Ru_id=it) | Q(nv_Ru_id=it, nature="C"))
        .order_by("-date")
        .first()
    )
    operateurs = SystEff(it).exclude(it=it)
    if der:
        derniere = der.date
        historique = (
            declaration_effectif.objects
            .filter(
                Q(Ru_id=it, nature__in=["C", "D", "A", "V"]) |
                Q(nv_Ru_id=it, nature="C"),
                date__lte=derniere,
            )
            .order_by("collaborateur_it_id", "-date", "-id")
        )
        dernier_etat_par_collab = {}
        for decl in historique:
            cid = decl.collaborateur_it_id
            if cid in dernier_etat_par_collab:
                continue
            if decl.nature == "C":
                etat = "inclure" if decl.nv_Ru_id == it else "exclure"
            elif decl.nature == "D":
                etat = "exclure"
            else:
                etat = "inclure"

            dernier_etat_par_collab[cid] = etat

        liste_exclure = {c for c, etat in dernier_etat_par_collab.items() if etat == "exclure"}
        liste_inclure = {c for c, etat in dernier_etat_par_collab.items() if etat == "inclure"}

        operateurs_finaux = Collaborateur.objects.filter(
            (Q(ru_it_id=it) & ~Q(it__in=liste_exclure)) | Q(it__in=liste_inclure)
        ).exclude(it=it)
    else:
        operateurs_finaux = operateurs
    operateurs_finaux = operateurs_finaux.exclude(lot__in=["C", "E"])
    return operateurs_finaux

#----------------------------------------------------------------------#
#Cette fct return les operateurs reel d'un responsable N+1  ds un jour
#----------------------------------------------------------------------#
def reelEff(it, date_reference=None, managers_its=None):
    """
    'managers_its' peut être passé par l'appelant (calculé une seule
    fois en dehors d'une boucle) pour éviter que cette fonction ne
    recalcule elle-même la liste de TOUS les managers à chaque appel.
    """
    if not it:
        return Collaborateur.objects.none()

    if managers_its is None:
        managers_its = get_managers_its()

    qs_declarations = declaration_effectif.objects.filter(Ru_id=it)
    if date_reference:
        qs_declarations = qs_declarations.filter(date__lte=date_reference)
    der = qs_declarations.order_by("-date").first()

    changements_tous = declaration_effectif.objects.filter(nature__in=["C", "D"], Ru_id=it)
    if date_reference:
        changements_tous = changements_tous.filter(date__lte=date_reference)
    liste_ch = set(changements_tous.values_list("collaborateur_it_id", flat=True))

    if der:
        derniere = der.date
        ajouters = declaration_effectif.objects.filter(nature="A", Ru_id=it, date=derniere)
        liste_a = set(ajouters.values_list("collaborateur_it_id", flat=True))
        valider = declaration_effectif.objects.filter(nature="V", Ru_id=it, date=derniere)
        liste_v = set(valider.values_list("collaborateur_it_id", flat=True))

        base_qs = Collaborateur.objects.filter(ru_it_id=it)

        operateurs_qs = base_qs.filter(~Q(it__in=liste_ch)).exclude(it=it)
        ajout_qs = Collaborateur.objects.filter(it__in=liste_a).exclude(it=it)
        valide_qs = Collaborateur.objects.filter(it__in=liste_v).exclude(it=it)

        operateurs_qs = (operateurs_qs | ajout_qs | valide_qs).distinct()
    else:
        operateurs_qs = Collaborateur.objects.filter(ru_it_id=it).exclude(it=it)

    operateurs_finaux = operateurs_qs.exclude(pk__in=managers_its)
    return operateurs_finaux

#-------------------------------------------------------#
#Cette fct return les operateurs  systeme d'un responsable N+1
#-------------------------------------------------------#
def SystEff(it, managers_its=None):
    """
    'managers_its' peut être passé par l'appelant (voir reelEff).
    """
    if not it:
        return Collaborateur.objects.none()

    if managers_its is None:
        managers_its = get_managers_its()

    operateur_syst = (
        Collaborateur.objects
        .filter(ru_it_id=it)
        .exclude(it__in=managers_its)
        .exclude(it=it)
    )

    return operateur_syst

#-------------------------------------------------------#
#Cette fct render vers dashboard de responsable N+1
#-------------------------------------------------------#
@role_required('N+1')
def operateurs(request):
    it = request.session.get("it")
    operateurs_list = reelEff(it)
    count = operateurs_list.count()

    items_per_page = 10
    paginator = Paginator(operateurs_list, items_per_page)
    page_number = request.GET.get('page', 1)

    try:
        operateurs_finaux = paginator.page(page_number)
    except PageNotAnInteger:
        operateurs_finaux = paginator.page(1)
    except EmptyPage:
        operateurs_finaux = paginator.page(paginator.num_pages)

    context = {
        "operateurs_finaux": operateurs_finaux,
        "count": count,
        "page_obj": operateurs_finaux,
        "paginator": paginator,
    }

    return render(request, "Collaborateur/N1/liste_operateur.html", context)


#-------------------------------------------------------#
#Cette fct utiliser pr filter la table de liste des operateurs
#-------------------------------------------------------#
def filter_tableau(request):
    text = request.GET.get('q', '')
    choix = request.GET.get('choix', '')
    time_param = request.GET.get('time', '')
    page_number = request.GET.get('page', 1)
    it = request.session.get("it")

    date_reference = None
    if time_param:
        try:
            date_reference = datetime.strptime(time_param, "%Y-%m-%d").date()
        except ValueError:
            date_reference = None

    operateurs = reelEff(it, date_reference)

    if choix and choix != "Tous les lots":
        operateurs = operateurs.filter(lot=choix)

    if text:
        operateurs = operateurs.filter(
            Q(nom_complete__icontains=text) |
            Q(matricule__icontains=text) |
            Q(it__icontains=text)
        )

    raw = operateurs.values("matricule", "it", "nom_complete", "lot","departement_id")

    PER_PAGE = 10
    paginator = Paginator(list(raw), PER_PAGE)

    try:
        page_obj = paginator.page(page_number)
    except Exception:
        page_obj = paginator.page(1)

    data = {
        "results": list(page_obj.object_list),
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "count": paginator.count,
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
    }

    return JsonResponse(data, safe=True)

#-------------------------------------------------------#
#Cette fct utiliser pr filter la table de liste de validation
#-------------------------------------------------------#

def filter_validation(request):
    text = request.GET.get('q', '')
    choix = request.GET.get('choix', '')
    it = request.session.get("it")
    ajoutes = request.GET.getlist('ajoutes')

    operateurs = rec(request)

    direct_ids = list(
        Collaborateur.objects.filter(ru_it__it=it).exclude(it=it).values_list('it', flat=True)
    )
    manager_direct_ids = set(
        Collaborateur.objects.filter(ru_it_id__in=direct_ids)
        .values_list('ru_it_id', flat=True)
        .distinct()
    )
    manager_direct_its = set(
        Collaborateur.objects.filter(it__in=manager_direct_ids)
        .values_list('it', flat=True)
    )
    operateurs = operateurs.exclude(it__in=manager_direct_its)

    if ajoutes:
        extra = Collaborateur.objects.filter(it__in=ajoutes)
        operateurs = (operateurs | extra).distinct()

    if choix and choix != "Tous les lots":
        operateurs = operateurs.filter(lot=choix)

    if text:
        operateurs = operateurs.filter(
            Q(nom_complete__icontains=text) |
            Q(matricule__icontains=text) |
            Q(it__icontains=text)
        )

    raw = list(operateurs.values("matricule", "it", "nom_complete", "lot"))

    data = {
        "results": raw,
        "count": len(raw),
    }

    return JsonResponse(data)

#-------------------------------------------------------#
#Cette fct utiliser pr recuperer les donners d'un op lors de sont ajout ds la liste de validation
#-------------------------------------------------------#

def operateur(request):
    it = request.GET.get('q', '').strip()
    utilisateur_it = request.session.get("it")

    try:
        op = Collaborateur.objects.get(it=it)
    except Collaborateur.DoesNotExist:
        return JsonResponse({"error": "Opérateur introuvable."}, status=404)
    except Collaborateur.MultipleObjectsReturned:
        return JsonResponse({"error": "Plusieurs opérateurs trouvés."}, status=409)

    if it == utilisateur_it:
        return JsonResponse({"error": "Vous ne pouvez pas utiliser votre utilisateur."}, status=404)
    derniere_decl = (
        declaration_effectif.objects
        .filter(collaborateur_it_id=it)
        .order_by("-date", "-id")
        .first()
    )
    a_declare_depart = derniere_decl is not None and derniere_decl.nature == "D"
    if not a_declare_depart and op.ru_it_id == utilisateur_it:
        return JsonResponse({"error": "Cet opérateur appartient déjà à votre lot."}, status=400)

    operateur_count = Collaborateur.objects.filter(ru_it_id=it).count()
    if operateur_count > 0:
        return JsonResponse(
            {"error": "Ce n'est pas un opérateur (il est responsable d'autres collaborateurs)."},
            status=400
        )

    data = {
        "matricule": op.matricule,
        "it": op.it,
        "nom_complete": op.nom_complete,
        "lot": op.lot,
    }
    return JsonResponse(data)

#-------------------------------------------------------#
#Cette fct return liste des op pr un jour
#-------------------------------------------------------#
def liste_par_jour(request):
    jour=request.GET.get("time","")
    it=request.session.get("it")
    operateurs=declaration_effectif.objects.filter(Q(Ru_id=it,date=jour,nature__in=["V","A"])&~Q(collaborateur_it__it=it))
    raw = operateurs.values(
            "collaborateur_it__matricule",
            "collaborateur_it_id",
            "collaborateur_it__nom_complete",
            "collaborateur_it__lot"
        )

    data = [
            {
                "matricule": d["collaborateur_it__matricule"],
                "it":d["collaborateur_it_id"],
                "nom_complete": d["collaborateur_it__nom_complete"],
                "lot": d["collaborateur_it__lot"]
            }
            for d in raw
        ]

    return JsonResponse(data ,safe=False)

#-------------------------------------------------------#
#Cette fct return la liste des N+1 d'un N+2
#-------------------------------------------------------#
def Ru_Rg(it):
    a_des_subordonnes = Collaborateur.objects.filter(
        ru_it_id=OuterRef('it')
    ).exclude(it=it)

    return Collaborateur.objects.filter(
        ru_it_id=it
    ).exclude(
        it=it
    ).annotate(
        has_sub=Exists(a_des_subordonnes)
    ).filter(has_sub=True)


#-------------------------------------------------------#
# Page : liste des collaborateurs directs + RU du N2
#-------------------------------------------------------#

def liste_N1_par_N2(request):
    it = request.session.get("it")
    ru_list = Ru_Rg(it)
    ru_its = set(ru_list.values_list("it", flat=True))

    col = Collaborateur.objects.filter(ru_it_id=it).exclude(it__in=ru_its)

    return render(
        request,
        "Collaborateur/N2/liste_N1.html",
        {"operateurs": ru_list, "col": col},
    )


#-------------------------------------------------------#
# Endpoint AJAX : collaborateurs directs d'un RU donné
#-------------------------------------------------------#

def collaborateurs_par_ru(request, ru_it):
    it = request.session.get("it")

    ru = Ru_Rg(it).filter(it=ru_it).first()
    if not ru:
        return JsonResponse({"error": "Responsable introuvable"}, status=404)

    collaborateurs = Collaborateur.objects.filter(
        ru_it_id=ru_it
    ).exclude(it=ru_it)

    data = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
        }
        for c in collaborateurs
    ]

    return JsonResponse({
        "ru_nom": ru.nom_complete,
        "count": len(data),
        "collaborateurs": data,
    })

def rechercher_N1_par_N2(request):
    """
    FIX PERF : l'ancienne version faisait un .count() en base pour
    CHAQUE collaborateur direct du N+2 (N+1 requêtes). On calcule
    maintenant en 2 requêtes l'ensemble des 'it' qui ont eux-mêmes
    des subordonnés.
    """
    it = request.session.get("it")
    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    page_number = request.GET.get("page", 1)

    collab = Collaborateur.objects.filter(ru_it_id=it).exclude(it=it)
    collab_ids = list(collab.values_list("it", flat=True))

    ids_avec_subordonnes = set(
        Collaborateur.objects.filter(ru_it_id__in=collab_ids)
        .exclude(ru_it_id=it)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )

    resultat = Collaborateur.objects.filter(it__in=ids_avec_subordonnes)

    if q:
        resultat = resultat.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        resultat = resultat.filter(lot=lot)

    paginator = Paginator(resultat, 15)
    page_obj = paginator.get_page(page_number)

    results = [
        {
            "matricule": op.matricule,
            "it": op.it,
            "nom_complete": op.nom_complete,
            "lot": op.lot,
        }
        for op in page_obj
    ]

    return JsonResponse({
        "results": results,
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })
#-------------------------------------------------------#
#Cette fct return la liste des N+2 d'un N+3
#-------------------------------------------------------#

def Rg_Dur(it):
    ccc_exists = Collaborateur.objects.filter(
        ru_it_id=OuterRef('it')
    ).exclude(it=it)
    cc_qs = Collaborateur.objects.filter(
        ru_it_id=OuterRef('it')
    ).exclude(it=it).annotate(
        has_ccc=Exists(ccc_exists)
    ).filter(has_ccc=True)

    return Collaborateur.objects.filter(
        ru_it_id=it
    ).exclude(
        it=it
    ).annotate(
        has_sub=Exists(cc_qs)
    ).filter(has_sub=True)


#-------------------------------------------------------#
#Cette fct return TOUS les 'it' sous 'it' donné, quel que soit
#le nombre de niveaux.
#
#FIX PERF : ancienne version = 1 requête PAR nœud de la hiérarchie
#(récursion en base). Nouvelle version = 1 requête pour TOUTE la
#table (via get_hierarchie_map, mise en cache), puis parcours en
#mémoire pur (0 requête supplémentaire).
#-------------------------------------------------------#
def get_tous_les_it_sous(it):
    mapping = get_hierarchie_map()
    vus = set()
    a_visiter = list(mapping.get(it, []))
    while a_visiter:
        courant = a_visiter.pop()
        if courant in vus:
            continue
        vus.add(courant)
        a_visiter.extend(mapping.get(courant, []))
    return vus


#-------------------------------------------------------#
#Cette fct rederige vers templete de liste des N+2 par N+3
#-------------------------------------------------------#
def liste_N2_par_N3(request):
    it = request.session.get("it")

    tous_les_it = get_tous_les_it_sous(it)
    tous_ru_it = get_managers_its()

    tous = Collaborateur.objects.filter(it__in=tous_les_it).select_related("ru_it")

    liste_finale = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "est_responsable": c.it in tous_ru_it,
            "ru_nom": c.ru_it.nom_complete if c.ru_it else "-",
        }
        for c in tous
    ]

    # RU distincts présents dans la liste, pour peupler le filtre "RU"
    ru_vus = {}
    for c in tous:
        if c.ru_it_id and c.ru_it_id not in ru_vus:
            ru_vus[c.ru_it_id] = c.ru_it.nom_complete if c.ru_it else c.ru_it_id
    ru_options = sorted(ru_vus.items(), key=lambda item: item[1] or "")

    return render(request, "Collaborateur/N3/liste_N2.html", {
        "operateurs": liste_finale,
        "count": len(liste_finale),
        "ru_options": ru_options,
    })


#-------------------------------------------------------#
#Endpoint AJAX : recherche + filtre lot + filtre rôle,
#sur TOUS les niveaux sous le N+3 connecté, avec le RU de chacun
#-------------------------------------------------------#
def rechercher_N2_par_N3(request):
    it = request.session.get("it")
    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    role = request.GET.get("role", "").strip()
    ru_it = request.GET.get("ru_it", "").strip()
    page_number = request.GET.get("page", 1)

    tous_les_it = get_tous_les_it_sous(it)
    tous_ru_it = get_managers_its()

    resultat = Collaborateur.objects.filter(it__in=tous_les_it).select_related("ru_it")

    if q:
        resultat = resultat.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        resultat = resultat.filter(lot=lot)
    if ru_it:
        resultat = resultat.filter(ru_it_id=ru_it)

    if role == "responsable":
        resultat = resultat.filter(it__in=tous_ru_it)
    elif role == "operateur":
        resultat = resultat.exclude(it__in=tous_ru_it)

    resultat = resultat.order_by("matricule")

    paginator = Paginator(resultat, 15)
    page_obj = paginator.get_page(page_number)

    results = [
        {
            "matricule": op.matricule,
            "it": op.it,
            "nom_complete": op.nom_complete,
            "lot": op.lot,
            "est_responsable": op.it in tous_ru_it,
            "ru_nom": op.ru_it.nom_complete if op.ru_it else "-",
        }
        for op in page_obj
    ]

    return JsonResponse({
        "results": results,
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })
#-------------------------------------------------------#
#Cette fct return la liste des N+1 pour N+3
#-------------------------------------------------------#
def liste_N1_pr_N3(it):
    n2 = Rg_Dur(it)
    rg_ids = list(n2.values_list("it", flat=True))
    has_sub = Collaborateur.objects.filter(
        ru_it_id=OuterRef('it')
    ).exclude(it=OuterRef('ru_it_id'))

    n1_qs = Collaborateur.objects.filter(
        ru_it_id__in=rg_ids
    ).exclude(
        it__in=rg_ids
    ).annotate(
        has_sub=Exists(has_sub)
    ).filter(has_sub=True)

    return set(n1_qs.values_list("it", flat=True))


#-------------------------------------------------------#
#Cette fct return la liste des N+3 d'un N+4
#-------------------------------------------------------#
def liste_N3_N4(it):
    """
    FIX PERF : a_deux_niveaux() faisait 2 requêtes par candidat.
    Utilise maintenant la map en cache (0 requête par candidat).
    """
    mapping = get_hierarchie_map()
    candidats = mapping.get(it, [])
    return [c for c in candidats if a_deux_niveaux(c)]

#-------------------------------------------------------#
# Verifie que un respo c'est un N+3
#-------------------------------------------------------#

def a_deux_niveaux(it):
    """
    FIX PERF : utilise la map en cache au lieu de 2 requêtes SQL.
    """
    mapping = get_hierarchie_map()
    enfants = mapping.get(it, [])
    if not enfants:
        return False
    return any(mapping.get(e) for e in enfants)


#-------------------------------------------------------#
#Cette fct return TOUS les "vrais" N+1 sous 'it', peu importe
#leur profondeur.
#
#FIX PERF : ancienne version = requêtes récursives en base.
#Nouvelle version = parcours en mémoire de get_hierarchie_map().
#-------------------------------------------------------#
def get_tous_les_n1(it, tous_ru_it=None):
    if tous_ru_it is None:
        tous_ru_it = get_managers_its()
    mapping = get_hierarchie_map()
    resultat = set()

    def _recurse(noeud):
        for enfant in mapping.get(noeud, []):
            if enfant not in tous_ru_it:
                continue
            sous_managers = [c for c in mapping.get(enfant, []) if c in tous_ru_it]
            if sous_managers:
                _recurse(enfant)
            else:
                resultat.add(enfant)

    _recurse(it)
    return resultat


def get_n1_et_n2_sous(it, tous_ru_it=None):
    """
    FIX PERF : même principe, entièrement en mémoire via la map.
    """
    if tous_ru_it is None:
        tous_ru_it = get_managers_its()
    mapping = get_hierarchie_map()
    n1_ids = set()
    n2_ids = set()

    def _recurse(noeud):
        for enfant in mapping.get(noeud, []):
            if enfant not in tous_ru_it:
                continue
            sous_managers = [c for c in mapping.get(enfant, []) if c in tous_ru_it]
            if sous_managers:
                n2_ids.add(enfant)
                _recurse(enfant)
            else:
                n1_ids.add(enfant)

    _recurse(it)
    return n1_ids, n2_ids


#-------------------------------------------------------#
#Effectif RÉEL basé sur les déclarations (dernier état connu)
#-------------------------------------------------------#
def get_dernieres_declarations():
    derniers_ids = (
        declaration_effectif.objects
        .values("collaborateur_it")
        .annotate(dernier_id=Max("id"))
        .values_list("dernier_id", flat=True)
    )
    declarations = (
        declaration_effectif.objects
        .filter(id__in=derniers_ids)
        .select_related("collaborateur_it", "Ru", "nv_Ru")
    )
    return {d.collaborateur_it_id: d for d in declarations if d.collaborateur_it_id}


def get_ru_reel_et_departs():
    """
    FIX PERF : mis en cache. Cette fonction scanne toute la table
    declaration_effectif + toute la table Collaborateur ; elle était
    appelée à chaque frappe clavier dans rechercher_N3_par_N4.
    Invalider via invalider_cache_hierarchie() après toute nouvelle
    déclaration (voir declaration_effectif.views.valider()).
    """
    cached = cache.get("ru_reel_et_departs")
    if cached is not None:
        return cached

    dernieres = get_dernieres_declarations()
    ru_reel = {}
    departs = set()

    for it_collab, decl in dernieres.items():
        if decl.nature == "D":
            departs.add(it_collab)
        elif decl.nature == "C" and decl.nv_Ru_id:
            ru_reel[it_collab] = decl.nv_Ru_id
        elif decl.Ru_id:
            ru_reel[it_collab] = decl.Ru_id

    resultat = (ru_reel, departs)
    cache.set("ru_reel_et_departs", resultat, CACHE_TTL)
    return resultat


def get_tous_les_it_sous_reel(it):
    ru_reel, departs = get_ru_reel_et_departs()

    enfants_par_ru = {}
    for c in Collaborateur.objects.exclude(it__in=departs):
        ru_effectif = ru_reel.get(c.it, c.ru_it_id)
        if ru_effectif:
            enfants_par_ru.setdefault(ru_effectif, []).append(c.it)

    def _recurse(it_courant, vus):
        niveau = set(enfants_par_ru.get(it_courant, [])) - vus
        if not niveau:
            return set()
        vus |= niveau
        tous = set(niveau)
        for sous_it in niveau:
            tous |= _recurse(sous_it, vus)
        return tous

    return _recurse(it, set())


# Collaborateur/views.py

def respo_N4(request):
    it = request.session.get("it")
    if not it:
        return redirect("login")

    tous_les_it = get_tous_les_it_sous_reel(it)
    tous_ru_it = get_managers_its()

    N3_qs = Collaborateur.objects.filter(it__in=tous_les_it).order_by("nom_complete")
    nbr = N3_qs.count()

    ru_it_ids = (
        N3_qs.exclude(ru_it_id__isnull=True)
        .values_list("ru_it_id", flat=True)
        .distinct()
    )
    responsables = Collaborateur.objects.filter(it__in=ru_it_ids).values("it", "nom_complete")
    paginator = Paginator(N3_qs, 10)
    page_obj = paginator.get_page(1)

    ru_ids_page = [c.ru_it_id for c in page_obj.object_list if c.ru_it_id]
    noms_par_it = dict(
        Collaborateur.objects.filter(it__in=ru_ids_page).values_list("it", "nom_complete")
    )

    N3 = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "est_responsable": c.it in tous_ru_it,
            "ru_nom": noms_par_it.get(c.ru_it_id, "-"),
        }
        for c in page_obj.object_list
    ]

    return render(request, "Collaborateur/N4/liste_N3.html", {
        "N3": N3,
        "nbr": nbr,
        "responsables": responsables,
    })

def rechercher_N3_par_N4(request):
    """
    FIX PERF : noms_par_it ne charge plus TOUTE la table Collaborateur
    à chaque appel AJAX — seulement les RU réellement présents sur la
    page courante.
    """
    it = request.session.get("it")
    if not it:
        return JsonResponse({"error": "unauthorized"}, status=401)

    q = request.GET.get("q", "").strip()
    lot = request.GET.get("choix", "").strip()
    ru_it = request.GET.get("ru_it", "").strip()
    page_number = request.GET.get("page", 1)

    tous_les_it = get_tous_les_it_sous_reel(it)
    queryset = Collaborateur.objects.filter(it__in=tous_les_it)

    if q:
        queryset = queryset.filter(
            Q(matricule__icontains=q) | Q(nom_complete__icontains=q)
        )
    if lot:
        queryset = queryset.filter(lot=lot)
    if ru_it:
        queryset = queryset.filter(ru_it_id=ru_it)

    tous_ru_it = get_managers_its()

    paginator = Paginator(queryset.order_by("nom_complete"), 10)
    page_obj = paginator.get_page(page_number)

    ru_ids_page = [c.ru_it_id for c in page_obj.object_list if c.ru_it_id]
    noms_par_it = dict(
        Collaborateur.objects.filter(it__in=ru_ids_page).values_list("it", "nom_complete")
    )

    results = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "est_responsable": c.it in tous_ru_it,
            "ru_nom": noms_par_it.get(c.ru_it_id, "-"),
        }
        for c in page_obj.object_list
    ]

    return JsonResponse({
        "results": results,
        "count": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
    })


def verifier(request):
    nv = request.GET.get("q", "").strip()

    if not Collaborateur.objects.filter(it=nv).exists():
        return JsonResponse({"valide": False, "erreur": "Identifiant introuvable."})

    est_ru = Collaborateur.objects.filter(ru_it_id=nv).exists()
    if not est_ru:
        return JsonResponse({"valide": False, "erreur": "Cet identifiant n'est pas un RU."})

    return JsonResponse({"valide": True})


def get_effectif_reel_ids(departement_ids, at_date):
    derniere_decl_id = (
        declaration_effectif.objects
        .filter(collaborateur_it=OuterRef('collaborateur_it'), date__lte=at_date)
        .order_by('-date', '-id')
        .values('id')[:1]
    )
    dernieres_declarations = (
        declaration_effectif.objects
        .filter(date__lte=at_date)
        .annotate(latest_id=Subquery(derniere_decl_id))
        .filter(id=F('latest_id'))
    )

    exclu1_ids = set(
        dernieres_declarations
        .filter(collaborateur_it__departement_id__in=departement_ids, nature="D")
        .values_list("collaborateur_it_id", flat=True)
    )

    exclu2_ids = set(
        dernieres_declarations
        .filter(collaborateur_it__departement_id__in=departement_ids, nature="C")
        .exclude(nv_Ru__departement_id__in=departement_ids)
        .values_list("collaborateur_it_id", flat=True)
    )

    inclu_ids = set(
        dernieres_declarations
        .filter(nv_Ru__departement_id__in=departement_ids, nature="C")
        .values_list("collaborateur_it_id", flat=True)
    )

    ids_departement = set(
        Collaborateur.objects.filter(departement_id__in=departement_ids).values_list("it", flat=True)
    )
    return (ids_departement - exclu1_ids - exclu2_ids) | inclu_ids


PER_PAGE = 20


def _parse_date(date_str):
    if not date_str:
        return timezone.now().date()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return timezone.now().date()


FIELD_BY_ROLE = {
    "HRBP": "HRBP_id",
    "DRH": "DRH_id",
    "ADMIN": "ADMIN_id",
    "PILOT": "PILOT_id",
}


def get_departement_ids_for_role(role, it):
    field = FIELD_BY_ROLE.get(role)
    if not field:
        return []
    return list(Departement.objects.filter(**{field: it}).values_list("id", flat=True))

def collaborateur(request):
    it = request.session.get("it")
    role = request.session.get("role")

    departement_ids = get_departement_ids_for_role(role, it)
    today = timezone.now().date()
    ids_total_r = get_effectif_reel_ids([dept.abreviation for dept in Departement.objects.filter(id__in=departement_ids)], today)
    total_r = len(ids_total_r)

    collaborateurs_qs = Collaborateur.objects.filter(it__in=ids_total_r).order_by("matricule")

    # RU = FK vers Collaborateur -> on filtre/affiche par son champ "it"
    ru_choices = (
        Collaborateur.objects.filter(it__in=ids_total_r, ru_it__isnull=False)
        .values_list("ru_it__it", "ru_it__nom_complete")
        .distinct()
        .order_by("ru_it__nom_complete")
    )

    paginator = Paginator(collaborateurs_qs, PER_PAGE)
    collaborateur_page = paginator.page(1)

    return render(
        request,
        "declaration_effectif/PILOT/collaborateurs.html",
        {
            "collaborateur": collaborateur_page,
            "total_r": total_r,
            "today": today.isoformat(),
            "ru_choices": ru_choices,
        },
    )


def collaborateur_api(request):
    it = request.session.get("it")
    role = request.session.get("role")

    departement_ids = get_departement_ids_for_role(role, it)
    if not departement_ids:
        raise Http404("Aucun département trouvé pour cet utilisateur.")

    search = request.GET.get("q", "").strip()
    lot = request.GET.get("lot", "").strip()
    ru = request.GET.get("ru", "").strip()
    selected_date = _parse_date(request.GET.get("date"))
    page_number = request.GET.get("page", 1)

    ids_total_r = get_effectif_reel_ids([dept.abreviation for dept in Departement.objects.filter(id__in=departement_ids)], selected_date)
    qs = Collaborateur.objects.filter(it__in=ids_total_r)

    if search:
        qs = qs.filter(
            Q(matricule__icontains=search) | Q(nom_complete__icontains=search)
        )
    if lot:
        qs = qs.filter(lot=lot)
    if ru:
        qs = qs.filter(ru_it__it=ru)

    qs = qs.select_related("departement", "ru_it").order_by("matricule")

    paginator = Paginator(qs, PER_PAGE)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else paginator.page(1)

    results = [
        {
            "matricule": c.matricule,
            "it": c.it,
            "nom_complete": c.nom_complete,
            "lot": c.lot,
            "ru_it": c.ru_it.it if c.ru_it else "",
            "ru_nom": c.ru_it.nom_complete if c.ru_it else "",
            "departement": c.departement.abreviation if c.departement else "",
        }
        for c in page_obj
    ]

    return JsonResponse({
        "results": results,
        "total_r": len(ids_total_r),
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else None,
        "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
    })