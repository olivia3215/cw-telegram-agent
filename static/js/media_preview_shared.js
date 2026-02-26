// Shared media preview helpers for admin console pages.
// Copyright (c) 2025-2026 Cindy's World LLC and contributors
// Licensed under the MIT License. See LICENSE.md for details.

/**
 * Open a media (image or video) in a fullscreen overlay. Used by Global Media Editor and Agents->Media.
 * @param {string} url - Source URL for the media
 * @param {'image'|'video'} type - Type of media
 */
function showMediaFullscreen(url, type) {
    const overlay = document.getElementById('media-fullscreen-overlay');
    const imgEl = document.getElementById('media-fullscreen-img');
    const videoEl = document.getElementById('media-fullscreen-video');
    if (!overlay || !imgEl || !videoEl) return;
    imgEl.style.display = 'none';
    imgEl.removeAttribute('src');
    videoEl.style.display = 'none';
    videoEl.removeAttribute('src');
    videoEl.pause();
    if (type === 'image' && url) {
        imgEl.src = url;
        imgEl.style.display = 'block';
    } else if (type === 'video' && url) {
        videoEl.src = url;
        videoEl.style.display = 'block';
        videoEl.play().catch(() => {});
    }
    overlay.style.display = 'block';
    document.body.style.overflow = 'hidden';
}

function closeMediaFullscreen() {
    const overlay = document.getElementById('media-fullscreen-overlay');
    const imgEl = document.getElementById('media-fullscreen-img');
    const videoEl = document.getElementById('media-fullscreen-video');
    if (overlay) overlay.style.display = 'none';
    if (imgEl) { imgEl.removeAttribute('src'); imgEl.style.display = 'none'; }
    if (videoEl) { videoEl.pause(); videoEl.removeAttribute('src'); videoEl.style.display = 'none'; }
    document.body.style.overflow = '';
}

async function adminDecompressGzip(data) {
    if (!('DecompressionStream' in window)) {
        throw new Error('DecompressionStream not supported in this browser');
    }

    const stream = new DecompressionStream('gzip');
    const writer = stream.writable.getWriter();
    const reader = stream.readable.getReader();

    writer.write(data);
    writer.close();

    const chunks = [];
    let totalLength = 0;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        totalLength += value.length;
    }

    const result = new Uint8Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
        result.set(chunk, offset);
        offset += chunk.length;
    }

    return result;
}

async function loadTGSAnimationsShared({
    selector,
    getMediaUrl,
    loadingLabel = 'Loading animated sticker...',
    errorLabel = 'Load Failed',
    downloadLabel = 'Download TGS',
}) {
    const tgsContainers = document.querySelectorAll(selector);

    for (const container of tgsContainers) {
        const mediaUrl = getMediaUrl(container);
        if (!mediaUrl) {
            continue;
        }

        try {
            const response = await fetchWithAuth(mediaUrl);
            if (!response.ok) {
                throw new Error(`Failed to fetch TGS file: ${response.status}`);
            }

            const tgsData = await response.arrayBuffer();
            let lottieJson;

            if (typeof pako !== 'undefined') {
                const decompressed = pako.inflate(new Uint8Array(tgsData), { to: 'string' });
                lottieJson = JSON.parse(decompressed);
            } else {
                const decompressedData = await adminDecompressGzip(tgsData);
                const jsonText = new TextDecoder().decode(decompressedData);
                lottieJson = JSON.parse(jsonText);
            }

            container.innerHTML = '';
            const animationContainer = document.createElement('div');
            animationContainer.style.width = '100%';
            animationContainer.style.height = '100%';
            animationContainer.style.display = 'flex';
            animationContainer.style.alignItems = 'center';
            animationContainer.style.justifyContent = 'center';
            container.appendChild(animationContainer);

            lottie.loadAnimation({
                container: animationContainer,
                renderer: 'svg',
                loop: true,
                autoplay: true,
                animationData: lottieJson
            });
        } catch (error) {
            if (error && error.message === 'unauthorized') {
                return;
            }

            container.innerHTML = `
                <div style="text-align: center; color: #dc3545;">
                    <div style="font-size: 16px; margin-bottom: 5px;">⚠️</div>
                    <div style="font-size: 11px;">${escapeHtml(errorLabel)}</div>
                    <div style="font-size: 10px; margin-top: 5px;">${escapeHtml(error.message || 'Unknown error')}</div>
                    <a href="${mediaUrl}" download style="color: #007bff; text-decoration: none; font-size: 10px; margin-top: 5px; display: block;">${escapeHtml(downloadLabel)}</a>
                </div>
            `;
        }
    }
}

