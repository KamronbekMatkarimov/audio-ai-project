async function fetchJSON(url, options = {}) {
    const res = await fetch(url, {
        headers: { "Accept": "application/json" },
        ...options,
    });
    if (!res.ok) {
        throw new Error(`Request failed: ${res.status}`);
    }
    return res.json();
}

async function loadCategories() {
    const categories = await fetchJSON("/api/categories");
    const tbody = document.querySelector("#categories-table tbody");
    const select = document.getElementById("override-category");
    tbody.innerHTML = "";
    select.innerHTML = '<option value="">Auto categorize</option>';

    categories.forEach(cat => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${cat.id}</td>
            <td>${cat.name}</td>
            <td>${cat.description || ""}</td>
            <td>${cat.keywords || ""}</td>
            <td>
                <button data-action="edit" data-id="${cat.id}">Edit</button>
                <button data-action="delete" data-id="${cat.id}">Delete</button>
            </td>
        `;
        tbody.appendChild(tr);

        const opt = document.createElement("option");
        opt.value = cat.id;
        opt.textContent = cat.name;
        select.appendChild(opt);
    });
}

async function loadAudio() {
    const items = await fetchJSON("/api/audio");
    const categories = await fetchJSON("/api/categories");
    const catMap = {};
    categories.forEach(c => catMap[c.id] = c.name);

    const tbody = document.querySelector("#audio-table tbody");
    tbody.innerHTML = "";
    items.forEach(item => {
        const catName = item.category_id ? (catMap[item.category_id] || "") : "";
        const preview = item.transcript.length > 80
            ? item.transcript.slice(0, 80) + "..."
            : item.transcript;
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${item.id}</td>
            <td>${item.original_filename}</td>
            <td>${catName}</td>
            <td>${item.confidence != null ? item.confidence.toFixed(3) : ""}</td>
            <td title="${item.transcript}">${preview}</td>
            <td>${new Date(item.created_at).toLocaleString()}</td>
            <td>
                <button data-action="delete-audio" data-id="${item.id}">Delete</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function loadSummary() {
    const data = await fetchJSON("/api/summary");
    const tbody = document.querySelector("#summary-table tbody");
    tbody.innerHTML = "";
    Object.entries(data.by_category).forEach(([name, stats]) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${name}</td>
            <td>${stats.count}</td>
            <td>${stats.avg_confidence}</td>
        `;
        tbody.appendChild(tr);
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const catForm = document.getElementById("category-form");
    const audioForm = document.getElementById("audio-form");

    catForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = document.getElementById("cat-name").value.trim();
        const desc = document.getElementById("cat-desc").value.trim();
        const keywords = document.getElementById("cat-keywords").value.trim();
        if (!name) return;
        await fetchJSON("/api/categories", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name,
                description: desc || null,
                keywords: keywords || null,
            }),
        });
        catForm.reset();
        await loadCategories();
    });

    document.querySelector("#categories-table tbody")
        .addEventListener("click", async (e) => {
            const btn = e.target.closest("button");
            if (!btn) return;
            const id = btn.getAttribute("data-id");
            const action = btn.getAttribute("data-action");
            if (action === "delete") {
                await fetchJSON(`/api/categories/${id}`, { method: "DELETE" });
                await loadCategories();
                await loadAudio();
                await loadSummary();
            } else if (action === "edit") {
                const name = prompt("New name:");
                const desc = prompt("New description (optional):");
                const keywords = prompt("New keywords (comma-separated, optional):");
                if (name) {
                    await fetchJSON(`/api/categories/${id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            name,
                            description: desc || null,
                            keywords: keywords || null,
                        }),
                    });
                    await loadCategories();
                    await loadAudio();
                    await loadSummary();
                }
            }
        });

    audioForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fileInput = document.getElementById("audio-file");
        if (!fileInput.files[0]) return;
        const formData = new FormData();
        formData.append("file", fileInput.files[0]);
        const override = document.getElementById("override-category").value;
        if (override) {
            formData.append("override_category_id", override);
        }
        await fetch("/api/audio", {
            method: "POST",
            body: formData,
        });
        audioForm.reset();
        await loadAudio();
        await loadSummary();
    });

    document.querySelector("#audio-table tbody")
        .addEventListener("click", async (e) => {
            const btn = e.target.closest("button");
            if (!btn) return;
            const id = btn.getAttribute("data-id");
            const action = btn.getAttribute("data-action");
            if (action === "delete-audio") {
                await fetchJSON(`/api/audio/${id}`, { method: "DELETE" });
                await loadAudio();
                await loadSummary();
            }
        });

    Promise.all([loadCategories(), loadAudio(), loadSummary()]).catch(console.error);
});

