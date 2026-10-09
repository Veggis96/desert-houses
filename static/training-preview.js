(() => {
    document.querySelectorAll('[data-training-preview]').forEach(preview => {
        const card = preview.closest('.building-card');
        const input = card.querySelector('input[name="amount"]');
        const costs = JSON.parse(preview.dataset.cost);
        const names = {iron: 'Iron', wood: 'Wood', water: 'Water', spice: 'Spice Sand', melange: 'Melange'};
        const update = () => {
            const amount = Number(input.value);
            if (!Number.isInteger(amount) || amount < 1 || amount > 999) {
                preview.querySelector('.training-total').textContent = 'Enter a whole number from 1 to 999.';
                preview.querySelector('.training-water').textContent = '';
                return;
            }
            const seconds = Math.max(Math.floor(Number(preview.dataset.trainingFactor) * amount), 3);
            preview.querySelector('.training-total').textContent = Object.entries(costs).filter(([, cost]) => cost > 0).map(([key, cost]) => `${names[key]} ${cost * amount}`).join(' · ') + ` · ${seconds}s training`;
            const added = Number(preview.dataset.upkeep) * amount;
            const rate = Number(preview.dataset.waterRate) - added;
            const warning = preview.querySelector('.training-water');
            warning.textContent = `Adds ${added.toFixed(1)} Water upkeep/hour. Projected balance at home: ${rate.toFixed(1)}/hour, including queued troops.`;
            warning.classList.toggle('water-warning', rate < 0 || amount > Number(preview.dataset.max));
            if (amount > Number(preview.dataset.max)) warning.textContent += ' You cannot afford this amount with the current stockpile.';
            if (rate < 0) {
                const remaining = Math.max(0, Number(preview.dataset.waterStock) - (costs.water || 0) * amount);
                warning.textContent += ` Water deficit: upgrade production. Current stock would last about ${(remaining / -rate).toFixed(1)} hours at that balance.`;
            }
        };
        preview.querySelector('.train-max').addEventListener('click', () => { input.value = preview.dataset.max; update(); });
        input.addEventListener('input', update);
        update();
    });
})();
