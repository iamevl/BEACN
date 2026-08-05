let discoveryElapsedTimer = null;


function formatDiscoveryElapsed(totalSeconds) {

    const safeSeconds = Math.max(
        0,
        Math.floor(Number(totalSeconds) || 0)
    );

    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const seconds = safeSeconds % 60;

    const paddedMinutes =
        String(minutes).padStart(2, '0');

    const paddedSeconds =
        String(seconds).padStart(2, '0');

    if (hours > 0) {
        return `${hours}:${paddedMinutes}:${paddedSeconds}`;
    }

    return `${paddedMinutes}:${paddedSeconds}`;
}


function stopDiscoveryElapsedTimer() {

    if (discoveryElapsedTimer !== null) {
        clearInterval(discoveryElapsedTimer);
        discoveryElapsedTimer = null;
    }

}


function startDiscoveryElapsedTimer(startedAt) {

    stopDiscoveryElapsedTimer();

    const elapsedElement =
        document.getElementById('discoveryElapsed');

    const startedAtMilliseconds =
        Date.parse(startedAt);

    if (
        !elapsedElement ||
        Number.isNaN(startedAtMilliseconds)
    ) {
        return;
    }

    const updateElapsed = () => {

        const elapsedSeconds = Math.floor(
            (Date.now() - startedAtMilliseconds) / 1000
        );

        elapsedElement.textContent =
            formatDiscoveryElapsed(elapsedSeconds);

    };

    updateElapsed();

    discoveryElapsedTimer =
        window.setInterval(updateElapsed, 1000);

}


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
        let discoveryStartedAt = null;

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

            if (discoveryRunning) {
                discoveryStartedAt =
                    check.details?.started_at || null;
            }

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

            const message = discoveryRunning
                ? `
                    Discovering network...
                    Elapsed
                    <span id="discoveryElapsed">00:00</span>
                  `
                : esc(check.message);

            return `
                <div class="health-check health-check-${check.state}">
                    <span
                      class="health-check-icon"
                      aria-hidden="true"
                    >
                        ${icon}
                    </span>

                    <span class="health-check-message">
                        ${message}
                    </span>

                    ${activity}
                </div>
            `;

        }).join('');

        document.getElementById('healthChecks').innerHTML = checks;

        if (discoveryStartedAt) {
            startDiscoveryElapsedTimer(discoveryStartedAt);
        } else {
            stopDiscoveryElapsedTimer();
        }
    }

    catch (err) {

        stopDiscoveryElapsedTimer();

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
