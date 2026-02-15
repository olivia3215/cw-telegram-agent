// Admin Console Media - Media browser, editor, pagination, and animations
// Copyright (c) 2025-2026 Cindy's World LLC and contributors
// Licensed under the MIT License. See LICENSE.md for details.

// Cached copy of media shown on the current page.
let currentPageMediaFiles = [];

function loadMediaFiles(directoryPath, preservePage = false) {
    // Show loading spinner
    showLoadingSpinner('media-container', 'Loading media files...');

    const savedPage = currentPage; // Save current page

    const encodedPath = encodeURIComponent(directoryPath);
    
    // Build query parameters
    const params = new URLSearchParams();
    params.append('directory', directoryPath);
    params.append('page', preservePage ? currentPage : 1);
    params.append('page_size', itemsPerPage);
    
    // Get limit value if set
    const mediaLimitInput = document.getElementById('media-limit');
    const limit = mediaLimitInput && mediaLimitInput.value.trim() ? mediaLimitInput.value.trim() : '';
    if (limit) {
        params.append('limit', limit);
    }
    
    // Add search query
    if (currentSearchQuery) {
        params.append('search', currentSearchQuery);
    }
    
    // Add media type filter
    if (currentMediaType && currentMediaType !== 'all') {
        params.append('media_type', currentMediaType);
    }
    
    fetchWithAuth(`${API_BASE}/media?${params.toString()}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError('media-container', data.error);
                return;
            }

            // Update pagination state from response
            if (data.pagination) {
                currentPage = data.pagination.page;
                currentTotalPages = data.pagination.total_pages;
                currentTotalItems = data.pagination.total_items;
            }

            // Process media files for display (add display names)
            const mediaFiles = data.media_files || [];
            currentPageMediaFiles = mediaFiles;
            for (const [stickerSet, items] of Object.entries(data.grouped_media || {})) {
                if (!Array.isArray(items) || items.length === 0) {
                    continue;
                }
                
                const firstMedia = items[0];
                let displayName = stickerSet;
                if (firstMedia && firstMedia.sticker_set_title && firstMedia.sticker_set_title !== stickerSet) {
                    displayName = `${stickerSet} (${firstMedia.sticker_set_title})`;
                }
                
                const isEmojiSet = firstMedia && firstMedia.is_emoji_set;
                
                // Add prefix to distinguish emoji sets from sticker sets
                if (isEmojiSet) {
                    displayName = `🎨 Emoji Set: ${displayName}`;
                } else if (stickerSet !== "Other Media" && !stickerSet.startsWith("Other Media -")) {
                    displayName = `📎 Sticker Set: ${displayName}`;
                }
                
                items.forEach(media => {
                    let baseDisplayName = stickerSet;
                    if (media.sticker_set_title && media.sticker_set_title !== stickerSet) {
                        baseDisplayName = `${stickerSet} (${media.sticker_set_title})`;
                    }
                    media.sticker_set_display = baseDisplayName;
                    media.sticker_set_display_with_prefix = displayName;
                });
            }

            // Display the media
            displayMediaPage(mediaFiles);
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            document.getElementById('media-container').innerHTML =
                `<div class="error">Error loading media files: ${escapeHtml(String(error))}</div>`;
        });
}

function displayMediaPage(mediaFiles) {
    const container = document.getElementById('media-container');

    // If no directory is selected, don't display anything
    if (!currentDirectory) {
        showLoading(container, 'Select a directory to view media files');
        document.getElementById('pagination-top').style.display = 'none';
        document.getElementById('pagination-bottom').style.display = 'none';
        updatePaginationControls();
        populatePageSelect();
        return;
    }

    if (mediaFiles.length === 0) {
        let message = 'No media files found';
        if (currentSearchQuery || currentMediaType !== 'all') {
            message = 'No media files match the current filters';
        }
        container.innerHTML = `<div class="no-results" style="text-align: center; padding: 40px 20px; color: #666; font-size: 14px;">${message}</div>`;
        document.getElementById('pagination-top').style.display = 'none';
        document.getElementById('pagination-bottom').style.display = 'none';
        updatePaginationControls();
        populatePageSelect();
        return;
    }

    // Build HTML for current page
    let html = '';
    let lastStickerSet = null;
    
    mediaFiles.forEach(media => {
        // Add sticker set header if it changed
        if (media.sticker_set_display !== lastStickerSet) {
            if (lastStickerSet !== null) {
                html += '</div>'; // Close previous grid
            }
            const headerText = media.sticker_set_display_with_prefix || media.sticker_set_display;
            html += `<h2 style="margin: 16px 0 8px 0; color: #2c3e50; font-size: 18px; font-weight: 600;">${headerText}</h2>`;
            html += '<div class="media-grid">';
            lastStickerSet = media.sticker_set_display;
        }
        html += createMediaItemHTML(media);
    });

    // Close the last grid if we opened one
    if (lastStickerSet !== null) {
        html += '</div>';
    }
    container.innerHTML = html;

    // Update pagination controls
    updatePaginationControls();
    populatePageSelect();

    // Populate move directory dropdowns
    populateMoveDirectoryDropdowns();

    // Load TGS animations (with a small delay to ensure DOM is updated)
    setTimeout(() => {
        loadTGSAnimations();
    }, 100);

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function updatePaginationControls() {
    const hasItems = currentTotalItems > 0 && currentDirectory;
    // Show/hide and enable/disable buttons
    const topContainer = document.getElementById('pagination-top');
    const bottomContainer = document.getElementById('pagination-bottom');
    if (topContainer) {
        toggle(topContainer, hasItems, 'flex');
    }
    if (bottomContainer) {
        toggle(bottomContainer, hasItems, 'flex');
    }

    // Update pagination info display
    const paginationInfo = document.getElementById('pagination-info');
    if (paginationInfo && hasItems) {
        let infoText = `Page ${currentPage} of ${currentTotalPages} (${currentTotalItems} item${currentTotalItems !== 1 ? 's' : ''})`;
        if (currentSearchQuery) {
            infoText += ` • Search: "${currentSearchQuery}"`;
        }
        if (currentMediaType && currentMediaType !== 'all') {
            const typeLabels = {
                'stickers': 'Stickers',
                'emoji': 'Emoji Sets',
                'video': 'Videos',
                'photos': 'Photos',
                'audio': 'Audio',
                'other': 'Other'
            };
            infoText += ` • Type: ${typeLabels[currentMediaType] || currentMediaType}`;
        }
        paginationInfo.textContent = infoText;
    }

    // Disable previous button on first page
    const prevDisabled = currentPage === 1;
    document.getElementById('prev-btn').disabled = prevDisabled;
    document.getElementById('prev-btn-bottom').disabled = prevDisabled;
    document.getElementById('prev-btn').style.opacity = prevDisabled ? '0.5' : '1';
    document.getElementById('prev-btn-bottom').style.opacity = prevDisabled ? '0.5' : '1';
    document.getElementById('prev-btn').style.cursor = prevDisabled ? 'not-allowed' : 'pointer';
    document.getElementById('prev-btn-bottom').style.cursor = prevDisabled ? 'not-allowed' : 'pointer';

    // Disable next button on last page
    const nextDisabled = currentPage === currentTotalPages || !hasItems || currentTotalPages === 0;
    document.getElementById('next-btn').disabled = nextDisabled;
    document.getElementById('next-btn-bottom').disabled = nextDisabled;
    document.getElementById('next-btn').style.opacity = nextDisabled ? '0.5' : '1';
    document.getElementById('next-btn-bottom').style.opacity = nextDisabled ? '0.5' : '1';
    document.getElementById('next-btn').style.cursor = nextDisabled ? 'not-allowed' : 'pointer';
    document.getElementById('next-btn-bottom').style.cursor = nextDisabled ? 'not-allowed' : 'pointer';
}

function setupPaginationControls() {
    const pageSelects = [
        document.getElementById('page-select'),
        document.getElementById('page-select-bottom'),
    ];

    pageSelects.forEach((select) => {
        if (!select) {
            return;
        }
        select.addEventListener('change', (event) => {
            const value = parseInt(event.target.value, 10);
            if (Number.isNaN(value)) {
                return;
            }
            jumpToPage(value);
        });
    });
}

function jumpToPage(page) {
    if (!currentDirectory) return;
    if (page < 1 || page > currentTotalPages) return;
    currentPage = page;
    loadMediaFiles(currentDirectory, true);
}

function clearMediaSearch() {
    const mediaSearchInput = document.getElementById('media-search');
    const clearSearchBtn = document.getElementById('clear-search-btn');
    if (mediaSearchInput) {
        mediaSearchInput.value = '';
    }
    if (clearSearchBtn) {
        clearSearchBtn.style.display = 'none';
    }
    currentSearchQuery = '';
    if (currentDirectory) {
        currentPage = 1;
        loadMediaFiles(currentDirectory);
    }
}

function populatePageSelect() {
    const hasItems = currentTotalItems > 0;
    const pageSelects = [
        document.getElementById('page-select'),
        document.getElementById('page-select-bottom'),
    ];

    pageSelects.forEach((select) => {
        if (!select) {
            return;
        }

        select.innerHTML = '';

        const placeholderOption = document.createElement('option');
        placeholderOption.value = '';
        placeholderOption.textContent = 'Go to page…';
        placeholderOption.disabled = true;
        placeholderOption.hidden = hasItems;
        select.appendChild(placeholderOption);

        if (hasItems) {
            for (let page = 1; page <= currentTotalPages; page += 1) {
                select.appendChild(createOption(String(page), `Page ${page}`));
            }

            select.value = String(currentPage);
            select.disabled = currentTotalPages <= 1;
        } else {
            select.value = '';
            select.disabled = true;
        }
    });
}

function previousPage() {
    if (!currentDirectory) return;
    if (currentPage > 1) {
        currentPage--;
        loadMediaFiles(currentDirectory, true);
    }
}

function nextPage() {
    if (!currentDirectory) return;
    if (currentPage < currentTotalPages) {
        currentPage++;
        loadMediaFiles(currentDirectory, true);
    }
}

function createMediaItemHTML(media) {
    const encodedDir = encodeURIComponent(currentDirectory);
    const mediaUrl = `${API_BASE}/media/${media.unique_id}?directory=${encodedDir}`;
    const mimeType = (media.mime_type || '').toLowerCase();
    let mediaElement = '';
    const fallbackTypeFromFile = (fileName) => {
        if (!fileName) return '';
        const lower = fileName.toLowerCase();
        if (lower.endsWith('.tgs')) return 'application/x-tgsticker';
        if (lower.endsWith('.webm')) return 'video/webm';
        if (lower.endsWith('.mp4')) return 'video/mp4';
        if (lower.endsWith('.gif')) return 'image/gif';
        if (lower.endsWith('.ogg')) return 'audio/ogg';
        if (lower.endsWith('.mp3')) return 'audio/mpeg';
        if (lower.endsWith('.m4a')) return 'audio/mp4';
        if (lower.endsWith('.wav')) return 'audio/wav';
        if (lower.endsWith('.png')) return 'image/png';
        if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
        return '';
    };
    const lowerMediaFile = (media.media_file || '').toLowerCase();
    let effectiveMime = mimeType || fallbackTypeFromFile(media.media_file);
    if (!effectiveMime && lowerMediaFile.endsWith('.tgs')) {
        effectiveMime = 'application/x-tgsticker';
    }
    if (effectiveMime === 'application/gzip' && lowerMediaFile.endsWith('.tgs')) {
        effectiveMime = 'application/x-tgsticker';
    }
    effectiveMime = (effectiveMime || '').toLowerCase();

    // Check for audio files first (before other media types to ensure proper detection)
    const isAudioFile = (
        effectiveMime.startsWith('audio') ||
        media.kind === 'audio' ||
        (!effectiveMime.startsWith('video') && lowerMediaFile && (
            lowerMediaFile.endsWith('.mp3') || 
            lowerMediaFile.endsWith('.m4a') || 
            lowerMediaFile.endsWith('.wav') || 
            lowerMediaFile.endsWith('.ogg') ||
            lowerMediaFile.endsWith('.opus') ||
            lowerMediaFile.endsWith('.flac')
        ))
    );

    // Video stickers (webm) must use <video> - they fail with TGS decompression
    const isVideoSticker = (media.kind === 'sticker' || media.kind === 'animated_sticker') &&
        effectiveMime.startsWith('video/');
    // Exclude audio files: audio/mp4 or mis-detected video/mp4 for .m4a must render as <audio>
    if (!isAudioFile && (isVideoSticker || effectiveMime.startsWith('video') || effectiveMime === 'image/gif')) {
            // Video content (mp4/webm/gif) - including video stickers
            const poster = media.thumbnail_url ? ` poster="${media.thumbnail_url}"` : '';
            mediaElement = `<video controls preload="metadata" style="width: 100%; height: auto;"${poster}>
                <source src="${mediaUrl}" type="${effectiveMime || 'video/webm'}">
                Your browser does not support the video tag.
            </video>`;
    } else if (effectiveMime.includes('tgs') || media.kind === 'animated_sticker') {
            // TGS files - Lottie animations, convert and display
            mediaElement = `<div style="position: relative; width: 100%; height: 200px; display: flex; align-items: center; justify-content: center;">
                <div id="tgs-player-${media.unique_id}" class="tgs-animation-container" style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;">
                    <div style="text-align: center; color: #666;">
                        <div style="font-size: 24px; margin-bottom: 10px;">🎭</div>
                        <div style="font-size: 12px; margin-bottom: 10px;">Loading TGS animation...</div>
                        <a href="${mediaUrl}" download style="color: #007bff; text-decoration: none; font-size: 11px;">Download TGS</a>
                    </div>
                </div>
            </div>`;
    } else if (isAudioFile) {
            // Audio files - Audio controls (checked before generic video)
            // Determine audio MIME type for the source tag
            let audioMimeType;
            if (!effectiveMime || !effectiveMime.startsWith('audio/')) {
                // Fallback: determine from file extension
                if (lowerMediaFile.endsWith('.ogg') || lowerMediaFile.endsWith('.opus')) {
                    audioMimeType = 'audio/ogg';
                } else if (lowerMediaFile.endsWith('.m4a')) {
                    audioMimeType = 'audio/mp4';
                } else if (lowerMediaFile.endsWith('.wav')) {
                    audioMimeType = 'audio/wav';
                } else if (lowerMediaFile.endsWith('.flac')) {
                    audioMimeType = 'audio/flac';
                } else {
                    audioMimeType = 'audio/mpeg'; // Default to mp3 since effectiveMime is not a valid audio type
                }
            } else {
                audioMimeType = effectiveMime;
            }
            mediaElement = `<audio controls preload="metadata" style="width: 100%; height: auto; min-height: 48px;">
                <source src="${mediaUrl}" type="${audioMimeType}">
                Your browser does not support the audio tag.
            </audio>`;
    } else if (mediaUrl) {
            mediaElement = `<img src="${mediaUrl}" alt="${media.sticker_name || media.unique_id}">`;
    } else {
        mediaElement = '<div style="color: #666;">No media file</div>';
    }

    // Format sticker name with emoji description
    const stickerName = media.sticker_name || media.unique_id;
    const displayName = (media.sticker_name && media.emoji_description)
        ? `${escapeHtml(media.sticker_name)} (${escapeHtml(media.emoji_description)})`
        : escapeHtml(stickerName);
    
    // Add document ID to title if it's not already shown
    const titleWithId = (displayName !== media.unique_id && media.unique_id)
        ? `${displayName} [${escapeHtml(media.unique_id)}]`
        : displayName;
    
    // Determine type display - show "custom emoji" for emoji sets
    let typeDisplay = escapeHtml(media.kind);
    if ((media.kind === 'sticker' || media.kind === 'animated_sticker') && media.is_emoji_set) {
        typeDisplay = media.kind === 'animated_sticker' ? 'custom emoji (animated)' : 'custom emoji';
    }

    return `
        <div class="media-item" id="media-item-${media.unique_id}" style="border: 1px solid #ddd; border-radius: 8px; margin-bottom: 20px; padding: 15px; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); width: 100%; max-width: 100%; box-sizing: border-box;">
            <div class="media-preview">
                ${mediaElement}
            </div>
            <div class="media-info">
                <h3 style="margin-top: 10px; margin-bottom: 10px;">${titleWithId}</h3>
                <p><strong>Type:</strong> ${typeDisplay}</p>
                ${(media.kind === 'sticker' || media.kind === 'animated_sticker') ? `<p><strong>Set:</strong> ${escapeHtml(media.sticker_set_display || media.sticker_set_name)}</p>` : ''}
                <p><strong>Status:</strong> ${escapeHtml(media.status)}</p>
                ${media.failure_reason ? `<p class="error">${escapeHtml(media.failure_reason)}</p>` : ''}

                <div class="description-edit">
                    <textarea id="desc-${media.unique_id}" placeholder="Enter description..." oninput="scheduleAutoSave('${escJsAttr(media.unique_id)}')" style="width: 100%; min-height: 80px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: inherit; resize: vertical; box-sizing: border-box;">${escapeHtml(media.description || '')}</textarea>
                    <div style="display: flex; align-items: center; margin-top: 8px; gap: 10px; flex-wrap: wrap;">
                        <span id="save-status-${media.unique_id}" style="font-size: 12px; color: #28a745;">Saved</span>
                        <button id="refresh-ai-btn-${media.unique_id}" onclick="refreshFromAI('${escJsAttr(media.unique_id)}')" style="padding: 4px 8px; font-size: 11px; background: #6c757d; color: white; border: none; border-radius: 3px; cursor: pointer;">Refresh from AI</button>
                        <select id="move-dir-${media.unique_id}" onchange="moveMedia('${escJsAttr(media.unique_id)}')" style="padding: 4px 6px; font-size: 11px; border: 1px solid #ddd; border-radius: 3px; background: white;">
                            <option value="">Move to...</option>
                        </select>
                        <button id="delete-btn-${media.unique_id}" onclick="deleteMedia('${escJsAttr(media.unique_id)}')" style="padding: 4px 8px; font-size: 11px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer;">Delete</button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

async function loadTGSAnimations() {
    // Find all TGS player containers
    const tgsContainers = document.querySelectorAll('[id^="tgs-player-"]');
    console.log(`Found ${tgsContainers.length} TGS containers to load`);

    for (const container of tgsContainers) {
        const uniqueId = container.id.replace('tgs-player-', '');
        const encodedDir = encodeURIComponent(currentDirectory);
        const mediaUrl = `${API_BASE}/media/${uniqueId}?directory=${encodedDir}`;
        console.log(`Loading TGS for ${uniqueId} from ${mediaUrl}`);

        try {
            // Fetch the TGS file
            const response = await fetchWithAuth(mediaUrl);
            if (!response.ok) {
                throw new Error(`Failed to fetch TGS file: ${response.status}`);
            }

            const tgsData = await response.arrayBuffer();
            console.log(`Fetched TGS data: ${tgsData.byteLength} bytes`);

            // Decompress the gzipped Lottie data
            let lottieJson;

            // Try pako first since DecompressionStream is unreliable
            if (typeof pako !== 'undefined') {
                try {
                    console.log('Attempting pako decompression...');
                    const decompressed = pako.inflate(new Uint8Array(tgsData), { to: 'string' });
                    lottieJson = JSON.parse(decompressed);
                    console.log('Pako decompression successful');
                } catch (pakoError) {
                    console.error('Pako decompression failed:', pakoError);
                    throw new Error('Failed to decompress TGS file with pako');
                }
            } else {
                // Fallback to DecompressionStream if pako not available
                try {
                    console.log('Attempting DecompressionStream decompression...');
                    const decompressedData = await decompressGzip(tgsData);
                    const jsonText = new TextDecoder().decode(decompressedData);
                    lottieJson = JSON.parse(jsonText);
                    console.log('DecompressionStream decompression successful');
                } catch (decompError) {
                    console.error('DecompressionStream failed:', decompError.message);
                    throw new Error('Failed to decompress TGS file - no suitable decompression method available');
                }
            }

            // Clear the loading content and create a new container for Lottie
            container.innerHTML = '';
            const animationContainer = document.createElement('div');
            animationContainer.style.width = '100%';
            animationContainer.style.height = '100%';
            animationContainer.style.display = 'flex';
            animationContainer.style.alignItems = 'center';
            animationContainer.style.justifyContent = 'center';
            animationContainer.style.backgroundColor = 'transparent';
            container.appendChild(animationContainer);

            // Initialize Lottie animation
            console.log('Initializing Lottie animation...');
            const animation = lottie.loadAnimation({
                container: animationContainer,
                renderer: 'svg',
                loop: true,
                autoplay: true,
                animationData: lottieJson
            });
            console.log('Lottie animation initialized successfully');

            // Handle animation errors
                    animation.addEventListener('error', (error) => {
                        console.error('Lottie animation error:', error);
                        container.innerHTML = `
                            <div style="text-align: center; color: #dc3545;">
                                <div style="font-size: 16px; margin-bottom: 5px;">⚠️</div>
                                <div style="font-size: 11px;">Animation Error</div>
                                <div style="font-size: 10px; margin-top: 5px;">${escapeHtml(error.message || 'Unknown error')}</div>
                            </div>
                        `;
                    });

            // Handle successful loading
            animation.addEventListener('DOMLoaded', () => {
                console.log('Lottie animation DOM loaded successfully');
            });

        } catch (error) {
            if (error && error.message === 'unauthorized') {
                return;
            }
            console.error(`Failed to load TGS animation for ${uniqueId}:`, error);
            container.innerHTML = `
                <div style="text-align: center; color: #dc3545;">
                    <div style="font-size: 16px; margin-bottom: 5px;">⚠️</div>
                    <div style="font-size: 11px;">Load Failed</div>
                    <div style="font-size: 10px; margin-top: 5px;">${escapeHtml(error.message || 'Unknown error')}</div>
                    <a href="${mediaUrl}" download style="color: #007bff; text-decoration: none; font-size: 10px; margin-top: 5px; display: block;">Download TGS</a>
                </div>
            `;
        }
    }
}

// Simple gzip decompression using browser APIs
async function decompressGzip(data) {
    // Check if DecompressionStream is supported
    if (!('DecompressionStream' in window)) {
        console.log('DecompressionStream not supported in this browser');
        throw new Error('DecompressionStream not supported in this browser');
    }

    try {
        console.log('Creating DecompressionStream...');
        const stream = new DecompressionStream('gzip');
        const writer = stream.writable.getWriter();
        const reader = stream.readable.getReader();

        console.log('Writing data to stream...');
        await writer.write(data);
        await writer.close();

        console.log('Reading decompressed data...');
        const chunks = [];
        let done = false;

        while (!done) {
            const { value, done: readerDone } = await reader.read();
            done = readerDone;
            if (value) {
                chunks.push(value);
            }
        }

        console.log(`Decompressed into ${chunks.length} chunks`);
        const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
        const result = new Uint8Array(totalLength);
        let offset = 0;

        for (const chunk of chunks) {
            result.set(chunk, offset);
            offset += chunk.length;
        }

        console.log(`Total decompressed size: ${result.length} bytes`);
        return result;
    } catch (error) {
        console.error('DecompressionStream error:', error);
        throw new Error(`Failed to decompress gzip data: ${error.message}`);
    }
}

function scheduleAutoSave(uniqueId) {
    // Clear existing timer for this textarea
    if (autoSaveTimers[uniqueId]) {
        clearTimeout(autoSaveTimers[uniqueId]);
    }

    // Set new timer for 1 second delay
    autoSaveTimers[uniqueId] = setTimeout(() => {
        updateDescription(uniqueId);
    }, 1000);

    // Update status to show "typing..."
    updateSaveStatus(uniqueId, 'typing');
}

function updateSaveStatus(uniqueId, status) {
    const statusElement = document.getElementById(`save-status-${uniqueId}`);
    if (!statusElement) return;

    switch (status) {
        case 'typing':
            statusElement.textContent = 'Saving...';
            statusElement.style.color = '#007bff';
            break;
        case 'saving':
            statusElement.textContent = 'Saving...';
            statusElement.style.color = '#007bff';
            break;
        case 'saved':
            statusElement.textContent = 'Saved';
            statusElement.style.color = '#28a745';
            break;
        case 'error':
            statusElement.textContent = 'Error';
            statusElement.style.color = '#dc3545';
            break;
        default:
            statusElement.textContent = 'Saved';
            statusElement.style.color = '#28a745';
    }
}

function updateDescription(uniqueId) {
    // Don't save if already saving
    if (savingStates[uniqueId]) {
        return;
    }

    const textarea = document.getElementById(`desc-${uniqueId}`);
    const description = textarea.value.trim();
    const encodedDir = encodeURIComponent(currentDirectory);

    // Mark as saving
    savingStates[uniqueId] = true;
    updateSaveStatus(uniqueId, 'saving');

    fetchWithAuth(`${API_BASE}/media/${uniqueId}/description?directory=${encodedDir}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ description: description })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            updateSaveStatus(uniqueId, 'error');
        } else {
            // Re-get textarea element for DOM traversal (use outer 'description' variable that was saved)
            const textareaEl = document.getElementById(`desc-${uniqueId}`);
            
            updateSaveStatus(uniqueId, 'saved');

            // Only show "curated" status if description was actually provided
            // (backend only sets curated status when description is non-empty)
            // Use the textarea to find the parent media item
            let mediaItem = null;
            if (textareaEl) {
                mediaItem = textareaEl.closest('.media-item');
            }
            
            // Fallback: try to find by ID
            if (!mediaItem) {
                mediaItem = document.getElementById(`media-item-${uniqueId}`);
            }

            if (mediaItem) {
                const statusElements = mediaItem.querySelectorAll('p');
                for (const p of statusElements) {
                    if (p.textContent.includes('Status:')) {
                        if (description) {
                            p.innerHTML = '<strong>Status:</strong> curated';
                        }
                        // If description is empty, don't change status - keep existing status from backend
                    } else if (p.classList.contains('error')) {
                        // Clear the error message (failure_reason) only if description was provided
                        if (description) {
                            p.remove();
                        }
                    }
                }
            }
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        updateSaveStatus(uniqueId, 'error');
    })
    .finally(() => {
        // Mark as no longer saving
        savingStates[uniqueId] = false;
    });
}

function refreshFromAI(uniqueId) {
    const button = document.getElementById(`refresh-ai-btn-${uniqueId}`);
    const textarea = document.getElementById(`desc-${uniqueId}`);
    const encodedDir = encodeURIComponent(currentDirectory);

    // Disable button and show loading state
    button.disabled = true;
    button.textContent = 'Generating...';
    button.style.background = '#007bff';

    fetchWithAuth(`${API_BASE}/media/${uniqueId}/refresh-ai?directory=${encodedDir}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error refreshing from AI: ' + data.error);
        } else {
            // Update the cached data in current page array.
            const mediaIndex = currentPageMediaFiles.findIndex(m => m.unique_id === uniqueId);
            if (mediaIndex !== -1) {
                currentPageMediaFiles[mediaIndex].description = data.description || null;
                currentPageMediaFiles[mediaIndex].status = data.status || 'ok';
                // Clear failure_reason if status is successful
                if (data.status && (
                    data.status === 'generated' || 
                    data.status === 'curated' ||
                    (data.status === 'budget_exhausted' && data.description)
                )) {
                    currentPageMediaFiles[mediaIndex].failure_reason = null;
                }
            }

            // Update the textarea with the new AI-generated description
            if (textarea) {
                textarea.value = data.description || '';
                autoGrowTextarea(textarea);
            }

            // Update all UI elements that might have changed
            // Use the textarea to find the parent media item
            let mediaItem = null;
            if (textarea) {
                mediaItem = textarea.closest('.media-item');
            }
            
            // Fallback: try to find by ID
            if (!mediaItem) {
                mediaItem = document.getElementById(`media-item-${uniqueId}`);
            }

            if (mediaItem) {
                // Update status display
                const statusElements = mediaItem.querySelectorAll('p');
                for (const p of statusElements) {
                    if (p.textContent.includes('Status:')) {
                        p.innerHTML = `<strong>Status:</strong> ${escapeHtml(data.status || 'ok')}`;
                    } else if (p.classList.contains('error')) {
                        // Remove failure_reason display if status is successful
                        // Successful statuses: generated, curated, budget_exhausted (for stickers with fallback)
                        const isSuccess = data.status && (
                            data.status === 'generated' || 
                            data.status === 'curated' ||
                            (data.status === 'budget_exhausted' && data.description)
                        );
                        if (isSuccess) {
                            p.remove();
                        }
                    }
                }

                // Update save status to show "Saved" since refresh endpoint saved the record
                const saveStatusEl = document.getElementById(`save-status-${uniqueId}`);
                if (saveStatusEl) {
                    saveStatusEl.textContent = 'Saved';
                    saveStatusEl.style.color = '#28a745';
                }
            } else {
                // If media item not found, reload the media list to get updated data
                console.warn(`Media item not found for ${uniqueId}, reloading media list`);
                loadMediaFiles(currentDirectory, true);
            }

            // Don't trigger auto-save - the refresh endpoint already saves the record
            // If description is empty, the status will remain as returned by the refresh (e.g., permanent_failure)
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error refreshing from AI: ' + error);
    })
    .finally(() => {
        // Re-enable button
        button.disabled = false;
        button.textContent = 'Refresh from AI';
        button.style.background = '#6c757d';
    });
}

function populateMoveDirectoryDropdowns() {
    // Get all move directory dropdowns
    const moveDropdowns = document.querySelectorAll('[id^="move-dir-"]');

    // Fetch available directories
    fetchWithAuth(`${API_BASE}/directories`)
        .then(response => response.json())
        .then(directories => {
            moveDropdowns.forEach(dropdown => {
                // Clear existing options except the first one
                while (dropdown.children.length > 1) {
                    dropdown.removeChild(dropdown.lastChild);
                }

                // Add directory options
                directories.forEach(dir => {
                    if (dir.path !== currentDirectory) { // Don't show current directory
                        const option = document.createElement('option');
                        option.value = dir.path;
                        option.textContent = dir.name;
                        dropdown.appendChild(option);
                    }
                });
            });
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            console.error('Error loading directories for move dropdowns:', error);
        });
}

function moveMedia(uniqueId) {
    const select = document.getElementById(`move-dir-${uniqueId}`);
    const targetDirectory = select.value;

    if (!targetDirectory) {
        return; // No selection made
    }

    const encodedCurrentDir = encodeURIComponent(currentDirectory);
    const encodedTargetDir = encodeURIComponent(targetDirectory);

    // Show loading state
    select.disabled = true;
    select.style.background = '#f8f9fa';

    fetchWithAuth(`${API_BASE}/media/${uniqueId}/move?from_directory=${encodedCurrentDir}&to_directory=${encodedTargetDir}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error moving media: ' + data.error);
            select.disabled = false;
            select.style.background = 'white';
            select.value = '';
        } else {
            // Reload the current directory to refresh the list, preserving current page
            loadMediaFiles(currentDirectory, true);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error moving media: ' + error);
        select.disabled = false;
        select.style.background = 'white';
        select.value = '';
    });
}

function deleteMedia(uniqueId) {
    const descElement = document.querySelector(`#desc-${uniqueId}`);
    let mediaName = uniqueId;
    
    if (descElement) {
        // Use the value if present, otherwise use unique_id (not placeholder)
        const descValue = descElement.value ? descElement.value.trim() : '';
        if (descValue) {
            mediaName = descValue;
        }
    }

    if (confirm(`Are you sure you want to delete ${mediaName}? This will permanently remove both the media file and description.`)) {
        const button = document.getElementById(`delete-btn-${uniqueId}`);
        const encodedDir = encodeURIComponent(currentDirectory);

        // Show loading state
        button.disabled = true;
        button.textContent = 'Deleting...';
        button.style.background = '#6c757d';

        fetchWithAuth(`${API_BASE}/media/${uniqueId}/delete?directory=${encodedDir}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error deleting media: ' + data.error);
                button.disabled = false;
                button.textContent = 'Delete';
                button.style.background = '#dc3545';
            } else {
                // Reload the current directory to refresh the list, preserving current page
                loadMediaFiles(currentDirectory, true);
            }
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            alert('Error deleting media: ' + error);
            button.disabled = false;
            button.textContent = 'Delete';
            button.style.background = '#dc3545';
        });
    }
}

function importStickerSet() {
    const stickerSetName = document.getElementById('sticker-set-name').value.trim();
    const statusDiv = document.getElementById('import-status');

    if (!stickerSetName) {
        statusDiv.innerHTML = '<div class="error">Please enter a sticker set name</div>';
        return;
    }

    if (!currentDirectory) {
        statusDiv.innerHTML = '<div class="error">Please select a directory first</div>';
        return;
    }

    statusDiv.innerHTML = '<div>Importing sticker set...</div>';

    fetchWithAuth(`${API_BASE}/import-sticker-set`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            sticker_set_name: stickerSetName,
            target_directory: currentDirectory
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            showError(statusDiv, data.error);
        } else {
            statusDiv.innerHTML = '<div style="color: #28a745;">Import completed!</div>';
            // Reload media files
            loadMediaFiles(currentDirectory);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            statusDiv.innerHTML = '<div class="error">Session expired. Please verify again.</div>';
            return;
        }
        showError(statusDiv, error);
    });
}

// Main tab switching logic - use event delegation
// The main tab bar is directly after the header, subtab bars are inside tab panels
