    document.addEventListener("DOMContentLoaded", () => {
        const resultat = document.getElementById("result");
        const counter = document.getElementById("counter");
        const searchInput = document.getElementById("search");
        const lotSelect = document.getElementById("choix");
        const resetBtn = document.getElementById("resetBtn");
        const pagination = document.getElementById("pagination");

        let currentPage = 1;

        function escapeHtml(text) {
            if (text === null || text === undefined) return '';
            const map = {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                '"': "&quot;",
                "'": "&#039;"
            };
            return String(text).replace(/[&<>"']/g, m => map[m]);
        }

        function renderPagination(page, numPages, hasPrev, hasNext) {
            if (numPages <= 1) {
                pagination.innerHTML = "";
                return;
            }

            let html = "";
            html += `<button class="page-btn" data-page="${page - 1}" ${hasPrev ? "" : "disabled"}>&laquo; Précédent</button>`;

            const start = Math.max(1, page - 2);
            const end = Math.min(numPages, page + 2);

            if (start > 1) {
                html += `<button class="page-btn" data-page="1">1</button>`;
                if (start > 2) html += `<span class="page-ellipsis">…</span>`;
            }

            for (let p = start; p <= end; p++) {
                html += `<button class="page-btn ${p === page ? "active" : ""}" data-page="${p}">${p}</button>`;
            }

            if (end < numPages) {
                if (end < numPages - 1) html += `<span class="page-ellipsis">…</span>`;
                html += `<button class="page-btn" data-page="${numPages}">${numPages}</button>`;
            }

            html += `<button class="page-btn" data-page="${page + 1}" ${hasNext ? "" : "disabled"}>Suivant &raquo;</button>`;

            pagination.innerHTML = html;

            pagination.querySelectorAll(".page-btn:not([disabled])").forEach(btn => {
                btn.addEventListener("click", () => {
                    currentPage = Number(btn.dataset.page);
                    applyFilters();
                });
            });
        }

        async function applyFilters() {
            const query = searchInput.value.trim();
            const lot = lotSelect.value;

            const params = new URLSearchParams();
            if (query) params.append("q", query);
            if (lot) params.append("choix", lot);
            params.append("page", currentPage);

            try {
                const response = await fetch(`/${endpoint}?${params.toString()}`);
                if (!response.ok) throw new Error("Erreur lors de la récupération des données");

                const data = await response.json();
                const results = data.results || [];

                if (counter) counter.textContent = data.count ?? results.length;

                if (results.length > 0) {
                    resultat.innerHTML = results.map(op => `
                        <tr class="table-row">
                            <td class="cell-matricule">${escapeHtml(op.matricule || '')}</td>
                            <td class="cell-utilisateur">${escapeHtml(op.it || '')}</td>
                            <td class="cell-nom">${escapeHtml(op.nom_complete || '')}</td>
                            <td class="cell-lot">${escapeHtml(op.lot || '')}</td>
                        </tr>
                    `).join("");
                } else {
                    resultat.innerHTML = `
                        <tr>
                            <td colspan="4" class="empty-row">
                                <i class="fas fa-search"></i> Aucun responsable d'unité trouvé
                            </td>
                        </tr>
                    `;
                }

                renderPagination(
                    data.page || 1,
                    data.num_pages || 1,
                    data.has_previous,
                    data.has_next
                );
            } catch (error) {
                console.error("Erreur de filtrage :", error);
                resultat.innerHTML = `
                    <tr>
                        <td colspan="4" class="empty-row error">
                            <i class="fas fa-exclamation-circle"></i> Une erreur s'est produite lors du filtrage
                        </td>
                    </tr>
                `;
                pagination.innerHTML = "";
            }
        }

        function resetFilters() {
            searchInput.value = "";
            lotSelect.value = "";
            currentPage = 1;
            applyFilters();
        }

        let searchTimeout;
        searchInput.addEventListener("input", () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                currentPage = 1;
                applyFilters();
            }, 300);
        });

        lotSelect.addEventListener("change", () => { currentPage = 1; applyFilters(); });
        resetBtn.addEventListener("click", resetFilters);

        applyFilters();
    });