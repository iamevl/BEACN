async function refreshHealth() {

    try {

        const r = await fetch('/api/health');
        const h = await r.json();

        document.getElementById('healthGreeting').textContent =
            h.greeting;

        const healthStatus =
            document.getElementById('healthStatus');

        const healthStatusLabel =
            healthStatus.querySelector('.health-status-label');

        const statusClass = {
            Healthy: 'health-status-green',
            Attention: 'health-status-amber',
            Degraded: 'health-status-amber',
            Critical: 'health-status-red'
        }[h.status] || 'health-status-unknown';

        healthStatus.className =
            `health-status ${statusClass}`;

        healthStatusLabel.textContent =
            h.status;
        document.getElementById('healthSummary').textContent =
            h.summary;

        const score = Math.max(
            0,
            Math.min(100, Number(h.score) || 0)
        );

        const scoreElement =
            document.getElementById('healthScore');

        const scoreBlock =
            document.getElementById('healthScoreBlock');

        /*
         * Continuous health colour:
         *   0   = red
         *   50  = amber
         *   100 = green
         */
        function interpolateColour(start, end, amount) {
            const value = Math.max(0, Math.min(1, amount));

            const channel = index => Math.round(
                start[index] +
                (end[index] - start[index]) * value
            );

            return `rgb(
                ${channel(0)},
                ${channel(1)},
                ${channel(2)}
            )`;
        }

        const red = [255, 114, 114];
        const amber = [246, 200, 95];
        const green = [94, 229, 166];

        const scoreColour = score <= 50
            ? interpolateColour(
                red,
                amber,
                score / 50
            )
            : interpolateColour(
                amber,
                green,
                (score - 50) / 50
            );
        scoreElement.textContent = score;
        scoreBlock.style.setProperty(
            '--health-score-colour',
            scoreColour
        );
        const dockerMetric =
            document.getElementById('dockerCount');

        if (dockerMetric) {
           dockerMetric.textContent =
               h.counts.containers_running;
        }
        const checks = h.checks.map(check => {

            const icon =
                check.state === 'ok'
                    ? '✅'
                    : check.state === 'warning'
                        ? '⚠️'
                        : check.state === 'critical'
                            ? '❌'
                            : 'ℹ️';

            const discoveryRunning =
                check.id === 'discovery' &&
                check.state === 'info' &&
                check.details?.running === true;

            const activity = discoveryRunning
                ? `
                    <span
                      class="discovery-spinner"
                      aria-label="Network discovery running"
                    >
                      <img
                        src="/static/branding/logos/beacn-primary-mark.svg"
                        alt=""
                        aria-hidden="true"
                      >
                    </span>
                  `
                : '';

            return `
                <div class="health-check health-check-${check.state}">
                    <span
                      class="health-check-icon"
                      aria-hidden="true"
                    >
                        ${icon}
                    </span>

                    <span class="health-check-message">
                        ${esc(check.message)}
                    </span>

                    ${activity}
                </div>
            `;

        }).join('');

        document.getElementById('healthChecks').innerHTML = checks;

    }

    catch (err) {

        console.error(err);

        const status =
            document.getElementById('healthStatus');

        const label =
            status?.querySelector('.health-status-label');

        if (status) {
            status.className =
                'health-status health-status-red';
        }

        if (label) {
            label.textContent = 'Unavailable';
        }

        document.getElementById('healthSummary').textContent =
            'Unable to retrieve network health.';

    }
}
