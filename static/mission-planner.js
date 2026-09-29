document.querySelectorAll("form[data-plan-mission]").forEach((form) => {
    const preview = document.createElement("div");
    preview.className = "raid-estimate";
    preview.setAttribute("aria-live", "polite");
    form.insertBefore(preview, form.querySelector('button[type="submit"]'));
    let timer, controller;
    async function update() {
        controller?.abort();
        controller = new AbortController();
        const current = controller;
        const query = new URLSearchParams(new FormData(form));
        query.delete("next");
        query.set("mission", form.dataset.planMission);
        preview.textContent = "Updating mission estimate…";
        try {
            const response = await fetch(`${form.dataset.planUrl}?${query}`, {signal: current.signal});
            const data = await response.json();
            if (current !== controller) return;
            preview.replaceChildren();
            if (!response.ok) { preview.textContent = data.error || "Estimate unavailable. Refresh and try again."; return; }
            const time = value => new Date(value).toLocaleTimeString();
            const items = [`Selected attack ${data.attack}`, `Durability ${data.durability}`, `Carry ${data.carry}`,
                `One way ${data.travel_seconds}s`, `Arrival ${time(data.arrival_at)}`,
                data.return_at ? `Expected home ${time(data.return_at)}` : "Stationed after arrival",
                data.known_defense === null ? "Enemy defense unknown" : `Known defense ${data.known_defense}`,
                data.intel_age_seconds === null ? "Unscouted" : `Intel age ${Math.floor(data.intel_age_seconds / 60)} min${data.stale ? " — outdated" : ""}`,
                data.risk, data.note];
            items.forEach(text => { const span = document.createElement("span"); span.textContent = text; preview.append(span); });
        } catch (error) {
            if (error.name !== "AbortError") preview.textContent = "Estimate unavailable. Refresh and try again.";
        }
    }
    form.addEventListener("input", () => { clearTimeout(timer); controller?.abort(); timer = setTimeout(update, 200); });
    update();
});
