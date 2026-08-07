// =====================================================================
// UTILITY FUNCTIONS
// =====================================================================

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

function escapeHtml(text) {
    if (text === null || text === undefined) return "";
    const map = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}

function slugifyIt(it) {
    return String(it)
        .toLowerCase()
        .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
}

function showAlert(message, type = 'error') {
    const alertEl = document.querySelector(".alert");
    if (alertEl) {
        alertEl.innerHTML = `<p style="color: ${type === 'error' ? '#DC2626' : '#16A34A'}; font-weight: 600; margin-top: 10px;">${escapeHtml(message)}</p>`;
    }
}

// =====================================================================
// STATE MANAGEMENT
// =====================================================================

let liste_valider = [];
let liste_C = [];
let liste_D = [];
let liste_A = [];

function retirerDesListes(it) {
    liste_valider = liste_valider.filter(id => id !== it);
    liste_D = liste_D.filter(id => id !== it);
    liste_C = liste_C.filter(o => o.it !== it);
}

function resetLigneStyle(ligne) {
    const btn_v = ligne.querySelector(".btn-valider");
    const btn_inv = ligne.querySelector(".btn-refuser");

    if (btn_v) {
        btn_v.innerHTML = "✓";
        btn_v.style.display = "inline-flex";
    }

    if (btn_inv) {
        btn_inv.innerHTML = "✗";
        btn_inv.style.display = "inline-flex";
    }
}

function appliquerEtatExistant(ligne, it) {
    const btn_v = ligne.querySelector(".btn-valider");
    const btn_inv = ligne.querySelector(".btn-refuser");

    if (liste_valider.includes(it)) {
        if (btn_v) btn_v.innerHTML = "✓ Validé";
        if (btn_inv) btn_inv.style.display = "none";
    } else if (liste_D.includes(it)) {
        if (btn_inv) btn_inv.innerHTML = "⊘ Départ";
        if (btn_v) btn_v.style.display = "none";
    } else if (liste_C.some(o => o.it === it)) {
        if (btn_inv) btn_inv.innerHTML = "↻ Changement";
        if (btn_v) btn_v.style.display = "none";
    }
}

function fermerModalEnSecurite(modal) {
    if (!modal) return;
    try {
        const instance = bootstrap.Modal.getOrCreateInstance(modal);
        instance.hide();
    } catch (error) {
        console.error("Erreur lors de la fermeture du modal:", error);
    }

    setTimeout(() => {
        const backdropsResiduels = document.querySelectorAll(".modal-backdrop");
        const modalOuvert = document.querySelector(".modal.show");
        if (!modalOuvert) {
            backdropsResiduels.forEach(b => b.remove());
            document.body.classList.remove("modal-open");
            document.body.style.removeProperty("overflow");
            document.body.style.removeProperty("padding-right");
        }
    }, 350);
}

function findRowByIT(it) {
    const tbody = document.getElementById("result");
    if (!tbody) return null;

    const rows = tbody.querySelectorAll("tr");
    for (let row of rows) {
        const itCell = row.querySelector(".utilisateur");
        if (itCell && itCell.textContent.trim() === it) {
            return row;
        }
    }
    return null;
}

function construireLigneOperateur(op) {
    const idSafe = slugifyIt(op.it);
    const it = op.it || "";

    const row = document.createElement("tr");
    row.id = `row-${idSafe}`;
    row.innerHTML = `
        <td>
            <input type="checkbox" class="op-checkbox" value="${escapeHtml(it)}">
        </td>
        <td class="matricule cell-mono">${escapeHtml(op.matricule)}</td>
        <td class="utilisateur cell-mono">${escapeHtml(op.it)}</td>
        <td class="nom">${escapeHtml(op.nom_complete)}</td>
        <td class="lot"><span class="chip chip-lot">${escapeHtml(op.lot)}</span></td>
        <td class="actions">
            <div class="action-buttons">
                <button class="btn-valider" data-matricule="${escapeHtml(it)}" title="Valider">✓</button>
                <button type="button" class="btn btn-primary btn-refuser" data-bs-toggle="modal" data-bs-target="#modal-${idSafe}" title="Refuser">✗</button>
                <button class="btn-modifier" data-matricule="${escapeHtml(it)}" title="Modifier">✎</button>
            </div>
            <div class="modal fade" id="modal-${idSafe}" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Refuser ${escapeHtml(op.nom_complete)}</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Fermer"></button>
                        </div>
                        <div class="modal-body">
                            <label for="motif-${idSafe}">Motif</label>
                            <select class="modal-motif" id="motif-${idSafe}">
                                <option value="depart">Départ</option>
                                <option value="changement">Changement d'affectation</option>
                            </select>
                            <div class="nv_Ru" style="display: none; margin-top: 10px;">
                                <label for="nouveau-ru-${idSafe}">Identifiant du nouveau RU</label>
                                <input type="text" id="nouveau-ru-${idSafe}" class="nouveau-ru-input" placeholder="Ex: RU-0234" required>
                                <p class="erreur-nv-ru" style="display: none; color: #dc2626; font-size: 12px; margin-top: 4px;">Ce champ est obligatoire.</p>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
                            <button type="button" class="btn btn-primary btn-confirmer" data-matricule="${escapeHtml(it)}">Confirmer</button>
                        </div>
                    </div>
                </div>
            </div>
        </td>
    `;

    appliquerEtatExistant(row, it);
    return row;
}

// =====================================================================
// GLOBAL EVENT LISTENERS
// =====================================================================

document.addEventListener("change", function (e) {
    if (e.target.classList.contains("modal-motif")) {
        const modal = e.target.closest(".modal");
        const nvRu = modal.querySelector(".nv_Ru");
        if (nvRu) {
            nvRu.style.display = (e.target.value === "changement") ? "block" : "none";
        }
    }
});

document.addEventListener("click", async function (e) {
    const target = e.target;

    // --- BUTTON VALIDATE (✓) ---
    if (target.classList.contains("btn-valider")) {
        const it = target.dataset.matricule;
        const ligne = target.closest("tr");
        const btn_inv = ligne.querySelector(".btn-refuser");

        retirerDesListes(it);
        liste_valider.push(it);

        target.innerHTML = "✓ Validé";
        if (btn_inv) btn_inv.style.display = "none";
        return;
    }

    // --- CONFIRM IN MODAL ---
    if (target.classList.contains("btn-confirmer")) {
        const modal = target.closest(".modal");
        const motif = modal.querySelector(".modal-motif");
        const it = target.dataset.matricule;
        const ligne = findRowByIT(it);

        if (!ligne) {
            showAlert("Ligne non trouvée");
            fermerModalEnSecurite(modal);
            return;
        }

        const btn_v = ligne.querySelector(".btn-valider");
        const btn_inv = ligne.querySelector(".btn-refuser");
        const erreur = modal.querySelector(".erreur-nv-ru");

        if (motif.value === "depart") {
            try {
                retirerDesListes(it);
                liste_D.push(it);

                if (btn_inv) btn_inv.innerHTML = "⊘ Départ";
                if (btn_v) btn_v.style.display = "none";
            } catch (error) {
                console.error("Erreur Départ:", error);
            } finally {
                fermerModalEnSecurite(modal);
            }
            return;
        }

        const nvRuInput = modal.querySelector(".nouveau-ru-input");
        const nvRu = nvRuInput.value.trim();

        if (nvRu === "") {
            if (erreur) {
                erreur.textContent = "Ce champ est obligatoire.";
                erreur.style.display = "block";
            }
            return;
        }

        try {
            const response = await fetch(`/verifier?q=${encodeURIComponent(nvRu)}`);
            const data = await response.json();

            if (data.valide) {
                if (erreur) erreur.style.display = "none";
                retirerDesListes(it);
                liste_C.push({ it: it, nvRu: nvRu });

                if (btn_inv) btn_inv.innerHTML = "↻ Changement";
                if (btn_v) btn_v.style.display = "none";

                nvRuInput.value = "";
                fermerModalEnSecurite(modal);
            } else if (erreur) {
                erreur.textContent = "Identifiant non valide.";
                erreur.style.display = "block";
            }
        } catch (error) {
            console.error("Erreur de vérification:", error);
        }
        return;
    }

    // --- BUTTON MODIFY (✎) ---
    if (target.classList.contains("btn-modifier")) {
        const it = target.dataset.matricule;
        retirerDesListes(it);

        const ligne = target.closest("tr");
        resetLigneStyle(ligne);
        return;
    }
});

// =====================================================================
// ADD OPERATOR MODAL
// =====================================================================

document.addEventListener("click", async function (e) {
    if (e.target.classList.contains("btn-confirmer1")) {
        const modal = e.target.closest(".modal");
        const inputElement = modal.querySelector(".nouveau-op-input");
        const it = inputElement.value.trim();

        if (!it) {
            showAlert("Veuillez entrer un identifiant");
            return;
        }

        if (findRowByIT(it) || liste_A.includes(it)) {
            showAlert("Cet opérateur figure déjà dans la liste");
            return;
        }

        try {
            const response = await fetch(`/operateur?q=${encodeURIComponent(it)}`);
            const data = await response.json();

            if (!response.ok) {
                showAlert(data.error || "Opérateur non trouvé");
                return;
            }

            liste_A.push(it);

            const tbody = document.getElementById("result");
            const newRow = construireLigneOperateur(data);
            tbody.appendChild(newRow);

            const btnValiderTout = document.getElementById("btnValiderTout");
            if (btnValiderTout) {
                const nbrActuel = Number(btnValiderTout.dataset.nbr) || 0;
                btnValiderTout.dataset.nbr = String(nbrActuel + 1);
            }

            inputElement.value = "";
            fermerModalEnSecurite(modal);
            showAlert("Opérateur ajouté avec succès", "success");
        } catch (error) {
            console.error("Erreur lors de l'ajout:", error);
            showAlert("Erreur lors de l'ajout de l'opérateur");
        }
    }
});

// =====================================================================
// SEARCH, CHECKBOXES & GLOBAL ACTIONS
// =====================================================================

document.addEventListener("DOMContentLoaded", function () {
    const searchInput = document.getElementById("chercher");
    const lotSelect = document.getElementById("choix");
    const postSelect = document.getElementById("choix2");
    const result = document.getElementById("result");

    if (searchInput && lotSelect && postSelect && result) {
        async function performSearch() {
            const query = searchInput.value.trim();
            const lot = lotSelect.value;
            const post = postSelect.value;

            try {
                const response = await fetch(
                    `/rechercher?q=${encodeURIComponent(query)}&choix=${encodeURIComponent(lot)}`
                );
                const data = await response.json();

                result.innerHTML = "";

                if (data.length > 0) {
                    data.forEach(op => {
                        const row = construireLigneOperateur(op);
                        result.appendChild(row);
                    });
                } else {
                    result.innerHTML = '<tr><td colspan="6" class="empty-row">Aucun opérateur trouvé.</td></tr>';
                }
            } catch (error) {
                console.error("Erreur lors de la recherche:", error);
            }
        }

        let debounceTimer = null;
        searchInput.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(performSearch, 300);
        });
        lotSelect.addEventListener("change", performSearch);
        postSelect.addEventListener("change", performSearch);
    }

    // Select All Checkbox logic
    // Function utilitaire pour remettre une ligne à son état initial
    function reinitialiserLigne(row, it) {
        // 1. Retirer l'IT de toutes les listes de validation/modification
        if (typeof retirerDesListes === 'function') {
            retirerDesListes(it);
        }

        // 2. Réinitialiser les styles CSS (couleurs, surbrillances, etc.)
        if (typeof resetLigneStyle === 'function') {
            resetLigneStyle(row);
        } else {
            row.classList.remove('table-success', 'table-warning', 'selected');
        }

        // 3. Réinitialiser l'état des boutons de la ligne
        const btnValider = row.querySelector('.btn-valider');
        const btnModifier = row.querySelector('.btn-modifier');

        if (btnValider) btnValider.disabled = false;
        if (btnModifier) btnModifier.disabled = false;
    }

    // ----------------------------------------------------
    // 1. Gestion du clic sur "Select All"
    // ----------------------------------------------------
    document.addEventListener("change", function (e) {
        if (e.target && e.target.id === 'selectAll') {
            const isChecked = e.target.checked;
            const checkboxes = document.querySelectorAll('.op-checkbox');

            checkboxes.forEach(cb => {
                cb.checked = isChecked;
                const row = cb.closest('tr');
                if (!row) return;

                const it = cb.value;
                const btnValider = row.querySelector('.btn-valider');

                if (isChecked) {
                    if (typeof retirerDesListes === 'function') retirerDesListes(it);
                    if (typeof liste_valider !== 'undefined') liste_valider.push(it);
                    if (btnValider) btnValider.click();
                } else {
                    // Retour à l'état initial
                    reinitialiserLigne(row, it);
                }
            });
        }
    });

    // ----------------------------------------------------
    // 2. Gestion du clic sur une case individuelle (.op-checkbox)
    // ----------------------------------------------------
    document.addEventListener("change", function (e) {
        if (e.target && e.target.classList.contains('op-checkbox')) {
            const cb = e.target;
            const row = cb.closest('tr');
            const it = cb.value;

            if (cb.checked) {
                // Si cochée : valider la ligne
                if (typeof retirerDesListes === 'function') retirerDesListes(it);
                if (typeof liste_valider !== 'undefined') liste_valider.push(it);

                const btnValider = row ? row.querySelector('.btn-valider') : null;
                if (btnValider) btnValider.click();
            } else {
                // Si décochée : RETOUR À L'ÉTAT INITIAL
                if (row) {
                    reinitialiserLigne(row, it);
                }
            }

            // Synchronisation du "Select All" (décocher si une case est décochée)
            const selectAll = document.getElementById('selectAll');
            if (selectAll) {
                const total = document.querySelectorAll('.op-checkbox').length;
                const coches = document.querySelectorAll('.op-checkbox:checked').length;
                selectAll.checked = (total > 0 && total === coches);
            }
        }
    });

    

    // Global Validation Submit
    const btnValiderTout = document.getElementById("btnValiderTout");
    if (btnValiderTout) {
        btnValiderTout.addEventListener("click", async function () {
            const totalActions = liste_valider.length + liste_C.length + liste_D.length;
            const totalOperators = Number(btnValiderTout.dataset.nbr);

            if (totalActions === 0) {
                showAlert("Aucune action à enregistrer");
                return;
            }

            if (totalActions !== totalOperators) {
                showAlert(`Tous les opérateurs doivent être traités (${totalActions}/${totalOperators})`);
                return;
            }

            try {
                const response = await fetch(VALIDER_URL, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": getCookie("csrftoken")
                    },
                    body: JSON.stringify({
                        valides: liste_valider,
                        changement: liste_C,
                        depart: liste_D,
                        ajouter: liste_A
                    })
                });

                const data = await response.json();
                if (response.ok && data.status === "valider") {
                    showAlert("Validation réussie!", "success");
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    showAlert(data.error || "Erreur lors de la validation");
                }
            } catch (error) {
                console.error("Erreur:", error);
                showAlert("Erreur serveur: " + error.message);
            }
        });
    }
});