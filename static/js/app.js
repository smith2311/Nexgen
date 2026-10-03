// Multi-speaker Real-time Translator - FIXED VERSION
// WebSocket + Audio Capture + Speaker Diarization

document.addEventListener('DOMContentLoaded', function() {
    // WebSocket connection setup
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/rs2t/`;
    let socket = null;

    const state = {
        isCapturing: false,
        totalTranslations: 0,
        totalWords: 0,
        languageCounts: { hindi: 0, hinglish: 0, english: 0 },
        systemStream: null,
        mediaRecorder: null,
        recordingChunks: [],
        currentBubble: null,
        chunkInterval: 3000,
        WORDS_PER_BUBBLE: 500,
        transcribeTimes: [],
        speakers: {},
        diarizationEnabled: false,
        targetLanguage: 'en',
        reconnectAttempts: 0,
        maxReconnectAttempts: 5
    };

    const elements = {
        captureBtn: null,
        clearBtn: null,
        statusText: null,
        translationsContainer: null,
        emptyState: null,
        translationCount: null,
        wordCount: null,
        translationCountBadge: null,
        debugInfo: null,
        processingTime: null,
        hindiCount: null,
        hinglishCount: null,
        englishCount: null,
        hindiBar: null,
        hinglishBar: null,
        englishBar: null,
        speakerList: null,
        speakerPanel: null,
        diarizationStatus: null,
        resetSpeakersBtn: null,
        targetLangSelect: null
    };

    // Initialize elements safely
    function initElements() {
        elements.captureBtn = document.getElementById('captureBtn');
        elements.clearBtn = document.getElementById('clearBtn');
        elements.statusText = document.getElementById('statusText');
        elements.translationsContainer = document.getElementById('translationsContainer');
        elements.emptyState = document.getElementById('emptyState');
        elements.translationCount = document.getElementById('translationCount');
        elements.wordCount = document.getElementById('wordCount');
        elements.translationCountBadge = document.getElementById('translationCountBadge');
        elements.debugInfo = document.getElementById('debugInfo');
        elements.processingTime = document.getElementById('processingTime');
        elements.hindiCount = document.getElementById('hindiCount');
        elements.hinglishCount = document.getElementById('hinglishCount');
        elements.englishCount = document.getElementById('englishCount');
        elements.hindiBar = document.getElementById('hindiBar');
        elements.hinglishBar = document.getElementById('hinglishBar');
        elements.englishBar = document.getElementById('englishBar');
        elements.speakerList = document.getElementById('speakerList');
        elements.speakerPanel = document.getElementById('speakerPanel');
        elements.diarizationStatus = document.getElementById('diarizationStatus');
        elements.resetSpeakersBtn = document.getElementById('resetSpeakersBtn');
        elements.targetLangSelect = document.getElementById('targetLangSelect');
    }

    initElements();

    // Initialize WebSocket connection
    function connectWebSocket() {
        try {
            socket = new WebSocket(wsUrl);
            
            socket.onopen = () => {
                console.log('✅ Connected to Django Channels WebSocket:', wsUrl);
                state.reconnectAttempts = 0;
                updateDebug('Connected • Speaker detection ready');
                
                sendMessage({
                    type: 'set_target_language',
                    target_language: state.targetLanguage
                });
            };
            
            socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    handleWebSocketMessage(data);
                } catch (e) {
                    console.error('❌ Error parsing message:', e);
                }
            };
            
            socket.onerror = (error) => {
                console.error('❌ WebSocket error:', error);
                updateDebug('Connection error');
            };
            
            socket.onclose = () => {
                console.log('🔌 WebSocket closed');
                updateDebug('Disconnected');
                
                if (state.reconnectAttempts < state.maxReconnectAttempts) {
                    state.reconnectAttempts++;
                    setTimeout(() => {
                        console.log(`🔄 Reconnecting... (${state.reconnectAttempts}/${state.maxReconnectAttempts})`);
                        connectWebSocket();
                    }, 2000);
                }
            };
        } catch (e) {
            console.error('❌ Failed to create WebSocket:', e);
        }
    }

    // Safe event listeners
    if (elements.captureBtn) {
        elements.captureBtn.addEventListener('click', () => {
            if (state.isCapturing) stopCapture();
            else startCapture();
        });
    }

    if (elements.clearBtn) {
        elements.clearBtn.addEventListener('click', clearAll);
    }

    if (elements.resetSpeakersBtn) {
        elements.resetSpeakersBtn.addEventListener('click', () => {
            if (confirm('Reset all speaker profiles? This will clear all learned voice patterns.')) {
                sendMessage({ type: 'reset_speakers' });
            }
        });
    }

    if (elements.targetLangSelect) {
        elements.targetLangSelect.addEventListener('change', (e) => {
            state.targetLanguage = e.target.value;
            sendMessage({
                type: 'set_target_language',
                target_language: state.targetLanguage
            });
            updateDebug(`🌍 Target language changed to ${getTargetLanguageName()}`);
        });
    }

    // Handle WebSocket messages
    function handleWebSocketMessage(data) {
        const messageType = data.type;
        
        switch (messageType) {
            case 'connection_status':
                handleConnectionStatus(data);
                break;
            case 'original_text':
                handleOriginalText(data);
                break;
            case 'translation_stream':
                handleTranslationStream(data);
                break;
            case 'speaker_updated':
                handleSpeakerUpdated(data);
                break;
            case 'speakers_reset':
                handleSpeakersReset();
                break;
            case 'audio_error':
                showError(data.message);
                break;
            case 'session_cleared':
                console.log('✅ Session cleared');
                break;
            case 'stats_update':
                console.log('📊 Stats:', data);
                break;
            case 'language_updated':
                console.log('✅ Language updated to:', data.target_language);
                updateDebug(`✅ Server confirmed: ${getTargetLanguageName()}`);
                break;
            default:
                console.log('Unknown message type:', messageType);
        }
    }

    function handleConnectionStatus(data) {
        console.log('📡 Connection status:', data);
        state.diarizationEnabled = data.diarization_enabled;

        if (!elements.diarizationStatus) return;

        if (data.diarization_enabled) {
            elements.diarizationStatus.innerHTML = '<i class="fas fa-check-circle"></i> Speaker Detection: ENABLED';
            elements.diarizationStatus.className = 'diarization-status enabled';
        } else {
            elements.diarizationStatus.innerHTML = '<i class="fas fa-times-circle"></i> Speaker Detection: DISABLED';
            elements.diarizationStatus.className = 'diarization-status disabled';
        }

        if (data.speakers && data.speakers.length > 0 && elements.speakerList) {
            data.speakers.forEach(speaker => {
                state.speakers[speaker.id] = speaker;
            });
            updateSpeakerList();
        }
    }

    function handleOriginalText(data) {
        console.log('📨 Original text:', data);
        
        const shouldCreateNewBubble = !state.currentBubble || 
            state.currentBubble.wordCount >= state.WORDS_PER_BUBBLE;
        
        if (shouldCreateNewBubble && state.currentBubble) {
            finalizeCurrentBubble();
        }
        
        if (!state.currentBubble) {
            createStreamingBubble(data);
        } else {
            appendToOriginalText(data);
        }
    }

    function handleTranslationStream(data) {
        if (!state.currentBubble) return;
        
        const translationEl = state.currentBubble.item.querySelector('#translation-text');
        const cursor = translationEl.querySelector('.typing-cursor');
        
        if (cursor) cursor.remove();
        
        translationEl.textContent += data.chunk;
        
        if (data.word_count !== undefined) {
            state.currentBubble.wordCount = state.currentBubble.originalWordCount + data.word_count;
            updateBubbleWordCount();
        }
        
        if (!data.is_final) {
            const newCursor = document.createElement('span');
            newCursor.className = 'typing-cursor';
            translationEl.appendChild(newCursor);
        } else {
            if (state.currentBubble.wordCount >= state.WORDS_PER_BUBBLE) {
                setTimeout(() => finalizeCurrentBubble(), 500);
            }
        }
        
        if (elements.translationsContainer) {
            elements.translationsContainer.scrollTop = elements.translationsContainer.scrollHeight;
        }
    }

    function handleSpeakerUpdated(data) {
        console.log('🔄 Speaker updated:', data);
        if (state.speakers[data.speaker_id]) {
            state.speakers[data.speaker_id].name = data.name;
            updateSpeakerList();
            
            document.querySelectorAll(`[data-speaker-id="${data.speaker_id}"]`).forEach(el => {
                const nameEl = el.querySelector('.bubble-speaker-name');
                if (nameEl) {
                    nameEl.textContent = data.name;
                }
            });
        }
    }

    function handleSpeakersReset() {
        console.log('🗑️ Speakers reset');
        state.speakers = {};
        updateSpeakerList();
    }

    // Send message to WebSocket
    function sendMessage(data) {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            if (!socket || socket.readyState === WebSocket.CLOSED) {
                connectWebSocket();
            }
            return;
        }
        socket.send(JSON.stringify(data));
    }

    // Send binary audio data
    function sendAudioData(audioData) {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            console.log('❌ WebSocket not connected, status:', socket ? socket.readyState : 'null');
            if (!socket || socket.readyState === WebSocket.CLOSED) {
                console.log('🔄 Attempting to reconnect WebSocket...');
                connectWebSocket();
            }
            return;
        }
        try {
            socket.send(audioData);
            console.log('✅ Audio data sent successfully');
        } catch (err) {
            console.error('❌ Error sending audio data:', err);
        }
    }

    function updateSpeakerList() {
        if (!elements.speakerList) return;
        const speakerList = elements.speakerList;
        
        if (Object.keys(state.speakers).length === 0) {
            speakerList.innerHTML = `
                <div class="no-speakers">
                    <i class="fas fa-user-slash"></i>
                    <p>No speakers detected yet</p>
                </div>
            `;
            return;
        }
        
        speakerList.innerHTML = '';
        
        Object.values(state.speakers).sort((a, b) => parseInt(a.id) - parseInt(b.id)).forEach(speaker => {
            const item = document.createElement('div');
            item.className = 'speaker-item';
            item.style.borderLeftColor = speaker.color;
            
            item.innerHTML = `
                <div class="speaker-avatar" style="background: ${speaker.color}">
                    ${speaker.id}
                </div>
                <div class="speaker-info">
                    <div class="speaker-name" data-speaker-id="${speaker.id}" contenteditable="true">
                        ${speaker.name}
                    </div>
                    <div class="speaker-stats">
                        ${speaker.count} segments
                    </div>
                </div>
            `;
            
            const nameEl = item.querySelector('.speaker-name');
            if (nameEl) {
                nameEl.addEventListener('focus', () => {
                    if (nameEl) nameEl.classList.add('editing');
                });
                nameEl.addEventListener('blur', () => {
                    if (nameEl) {
                        nameEl.classList.remove('editing');
                        const newName = nameEl.textContent.trim();
                        if (newName && newName !== speaker.name) {
                            sendMessage({
                                type: 'rename_speaker',
                                speaker_id: speaker.id,
                                name: newName
                            });
                        }
                    }
                });
                nameEl.addEventListener('keydown', (e) => {
                    if (nameEl && e.key === 'Enter') {
                        e.preventDefault();
                        nameEl.blur();
                    }
                });
            }
            
            speakerList.appendChild(item);
        });
    }

    function detectLanguageType(langName) {
        const lower = langName.toLowerCase();
        if (lower.includes('hinglish')) return 'hinglish';
        if (lower.includes('hindi')) return 'hindi';
        if (lower.includes('english')) return 'english';
        return 'other';
    }

    function getTargetLanguageName() {
        const langMap = {
            'en': 'English', 'hi': 'हिंदी', 'es': 'Spanish', 'fr': 'French',
            'de': 'German', 'zh': 'Chinese', 'ja': 'Japanese', 'ar': 'Arabic',
            'ru': 'Russian', 'pt': 'Portuguese', 'it': 'Italian', 'ko': 'Korean'
        };
        return langMap[state.targetLanguage] || 'English';
    }

    function createStreamingBubble(data) {
        if (!elements.emptyState || !elements.translationsContainer) return;
        
        elements.emptyState.style.display = 'none';
        
        const item = document.createElement('div');
        item.className = 'translation-item';
        
        if (data.speaker_id) {
            item.setAttribute('data-speaker-id', data.speaker_id);
        }
        
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        
        const langType = detectLanguageType(data.language);
        const langEmoji = langType === 'hindi' ? '🇮🇳' : 
                         langType === 'hinglish' ? '🇮🇳🇺🇸' : '🇺🇸';
        
        const wordCountInfo = data.word_count ? `${data.word_count} words` : '';
        const transcribeTimeInfo = data.transcribe_time ? `${data.transcribe_time}s` : '';
        
        let speakerHTML = '';
        if (data.speaker_id) {
            const speaker = state.speakers[data.speaker_id] || {
                id: data.speaker_id,
                name: data.speaker_name || `Speaker ${data.speaker_id}`,
                color: data.speaker_color || '#667eea',
                count: 1
            };
            
            state.speakers[data.speaker_id] = speaker;
            updateSpeakerList();
            
            speakerHTML = `
                <div class="bubble-speaker" style="border-left-color: ${speaker.color}">
                    <div class="bubble-speaker-avatar" style="background: ${speaker.color}">
                        ${speaker.id}
                    </div>
                    <div class="bubble-speaker-info">
                        <div class="bubble-speaker-name">${speaker.name}</div>
                        <div class="bubble-speaker-confidence">
                            ${Math.round((data.speaker_confidence || 0) * 500)}% match
                        </div>
                    </div>
                </div>
            `;
        }
        
        const targetLangName = getTargetLanguageName();
        
        item.innerHTML = `
            <div class="bubble streaming">
                ${speakerHTML}
                <div class="bubble-header">
                    <i class="fas fa-microphone"></i>
                    <span class="language-badge">${langEmoji} ${data.language}</span>
                    <span class="word-count-badge" id="bubble-word-count">${wordCountInfo}</span>
                    <span class="streaming-indicator">
                        <i class="fas fa-circle" style="font-size: 0.5rem;"></i>
                        Streaming...
                    </span>
                </div>
                <div>
                    <div class="bubble-original-label">📊 Original (${data.language})</div>
                    <div class="bubble-original" id="original-text">${data.text}</div>
                </div>
                <div>
                    <div class="bubble-translation-label">✅ ${targetLangName} Translation</div>
                    <div class="bubble-translation" id="translation-text"><span class="typing-cursor"></span></div>
                </div>
                <div class="timestamp" id="bubble-time">
                    <span><i class="fas fa-clock"></i> ${timeStr}</span>
                    <span style="margin-left: auto; font-size: 0.85rem; opacity: 0.7;">
                        ${transcribeTimeInfo} • ${Math.round(data.confidence * 100)}% confidence
                    </span>
                </div>
            </div>
        `;
        
        elements.translationsContainer.appendChild(item);
        state.currentBubble = {
            item: item,
            originalText: data.text,
            language: data.language,
            langType: langType,
            wordCount: data.word_count || 0,
            originalWordCount: data.word_count || 0,
            createdAt: Date.now(),
            speakerId: data.speaker_id
        };
        
        if (data.transcribe_time) {
            state.transcribeTimes.push(data.transcribe_time);
            updateAverageTime();
        }
        
        elements.translationsContainer.scrollTop = elements.translationsContainer.scrollHeight;
    }

    function appendToOriginalText(data) {
        if (!state.currentBubble) return;
        const originalEl = state.currentBubble.item.querySelector('#original-text');
        const newOriginal = state.currentBubble.originalText + " " + data.text;
        originalEl.textContent = newOriginal;
        state.currentBubble.originalText = newOriginal;
        
        if (data.word_count) {
            state.currentBubble.originalWordCount += data.word_count;
            state.currentBubble.wordCount = state.currentBubble.originalWordCount;
            updateBubbleWordCount();
        }
        
        if (data.transcribe_time) {
            state.transcribeTimes.push(data.transcribe_time);
            updateAverageTime();
        }
    }

    function updateBubbleWordCount() {
        if (!state.currentBubble) return;
        
        const wordCountEl = state.currentBubble.item.querySelector('#bubble-word-count');
        if (wordCountEl) {
            const count = state.currentBubble.wordCount;
            const limit = state.WORDS_PER_BUBBLE;
            const percentage = Math.min((count / limit) * 500, 500);
            
            wordCountEl.textContent = `${count}/${limit} words`;
            
            if (percentage >= 90) {
                wordCountEl.style.background = 'rgba(245, 101, 101, 0.3)';
                wordCountEl.style.color = '#fc8181';
            } else if (percentage >= 70) {
                wordCountEl.style.background = 'rgba(237, 137, 54, 0.3)';
                wordCountEl.style.color = '#f6ad55';
            } else {
                wordCountEl.style.background = 'rgba(102, 126, 234, 0.2)';
                wordCountEl.style.color = '#a0aec0';
            }
        }
    }

    function finalizeCurrentBubble() {
        if (!state.currentBubble) return;
        
        console.log('🔒 Finalizing bubble');
        
        const bubble = state.currentBubble.item.querySelector('.bubble');
        if (bubble) bubble.classList.remove('streaming');
        
        const header = state.currentBubble.item.querySelector('.bubble-header');
        const indicator = header ? header.querySelector('.streaming-indicator') : null;
        if (indicator) {
            indicator.innerHTML = '<i class="fas fa-check-circle"></i> Complete';
        }
        
        const cursor = state.currentBubble.item.querySelector('.typing-cursor');
        if (cursor) cursor.remove();
        
        const langType = state.currentBubble.langType;
        if (state.languageCounts[langType] !== undefined) {
            state.languageCounts[langType]++;
            updateLanguageBreakdown();
        }
        
        state.totalWords += state.currentBubble.wordCount;
        if (elements.wordCount) elements.wordCount.textContent = state.totalWords;
        
        state.totalTranslations++;
        updateCount();
        state.currentBubble = null;
    }

    function updateLanguageBreakdown() {
        if (!elements.hindiBar || !elements.hinglishBar || !elements.englishBar ||
            !elements.hindiCount || !elements.hinglishCount || !elements.englishCount) {
            return;
        }

        const total = state.languageCounts.hindi + 
                     state.languageCounts.hinglish + 
                     state.languageCounts.english;

        if (total === 0) {
            elements.hindiBar.style.width = '0%';
            elements.hinglishBar.style.width = '0%';
            elements.englishBar.style.width = '0%';
        } else {
            const hindiPercent = (state.languageCounts.hindi / total) * 100;
            const hinglishPercent = (state.languageCounts.hinglish / total) * 100;
            const englishPercent = (state.languageCounts.english / total) * 100;

            elements.hindiBar.style.width = hindiPercent + '%';
            elements.hinglishBar.style.width = hinglishPercent + '%';
            elements.englishBar.style.width = englishPercent + '%';
        }

        elements.hindiCount.textContent = state.languageCounts.hindi;
        elements.hinglishCount.textContent = state.languageCounts.hinglish;
        elements.englishCount.textContent = state.languageCounts.english;
    }

    function updateAverageTime() {
        if (state.transcribeTimes.length === 0 || !elements.processingTime) return;
        
        const avg = state.transcribeTimes.reduce((a, b) => a + b, 0) / state.transcribeTimes.length;
        elements.processingTime.textContent = avg.toFixed(1) + 's';
    }

    function updateCount() {
        if (!elements.translationCount || !elements.translationCountBadge) return;
        elements.translationCount.textContent = state.totalTranslations;
        elements.translationCountBadge.textContent = 
            `${state.totalTranslations} bubble${state.totalTranslations !== 1 ? 's' : ''}`;
    }

    async function startCapture() {
        try {
            if (!elements.statusText) return;
            
            // Wait for WebSocket to be connected
            if (!socket || socket.readyState !== WebSocket.OPEN) {
                updateDebug('⏳ Waiting for connection...');
                
                // Wait up to 5 seconds for connection
                let attempts = 0;
                while ((!socket || socket.readyState !== WebSocket.OPEN) && attempts < 50) {
                    await new Promise(resolve => setTimeout(resolve, 100));
                    attempts++;
                }
                
                if (!socket || socket.readyState !== WebSocket.OPEN) {
                    showError('Cannot start capture: WebSocket not connected');
                    updateDebug('❌ WebSocket connection failed');
                    return;
                }
            }
            
            updateDebug('🎙️ Requesting audio access...');
            
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: { width: 1, height: 1, frameRate: 1 },
                audio: {
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                    sampleRate: 16000
                }
            });

            const audioTracks = stream.getAudioTracks();

            if (!audioTracks.length) {
                showError('❌ No audio detected. Check "Share system audio".');
                stream.getTracks().forEach(t => t.stop());
                return;
            }

            stream.getVideoTracks().forEach(t => t.stop());
            const audioOnlyStream = new MediaStream([audioTracks[0]]);

            let options = { audioBitsPerSecond: 128000 };
            if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
                options.mimeType = 'audio/webm;codecs=opus';
            } else if (MediaRecorder.isTypeSupported('audio/webm')) {
                options.mimeType = 'audio/webm';
            }

            const recorder = new MediaRecorder(audioOnlyStream, options);
            state.recordingChunks = [];

            recorder.ondataavailable = (e) => {
                if (e.data && e.data.size > 0) {
                    state.recordingChunks.push(e.data);
                }
            };

            recorder.onstop = async () => {
                if (state.recordingChunks.length === 0) {
                    if (state.isCapturing && recorder.state === 'inactive') {
                        scheduleNextRecording();
                    }
                    return;
                }

                try {
                    const completeBlob = new Blob(state.recordingChunks, { type: options.mimeType });
                    const arrayBuffer = await completeBlob.arrayBuffer();
                    
                    if (arrayBuffer.byteLength > 10000) {
                        sendAudioData(arrayBuffer);
                        updateDebug(`📤 Sent ${(arrayBuffer.byteLength / 1024).toFixed(1)}KB (Target: ${getTargetLanguageName()})`);
                    }
                    
                    state.recordingChunks = [];
                    
                    if (state.isCapturing && recorder.state === 'inactive') {
                        scheduleNextRecording();
                    }
                } catch (err) {
                    console.error('❌ Error in recorder.onstop:', err);
                    if (state.isCapturing) {
                        scheduleNextRecording();
                    }
                }
            };

            function scheduleNextRecording() {
                try {
                    if (recorder.state === 'inactive' && state.isCapturing) {
                        setTimeout(() => {
                            if (state.isCapturing && recorder.state === 'inactive') {
                                recorder.start();
                                setTimeout(() => {
                                    if (state.isCapturing && recorder.state === 'recording') {
                                        recorder.stop();
                                    }
                                }, state.chunkInterval);
                            }
                        }, 100);
                    }
                } catch (err) {
                    console.error('❌ Error scheduling next recording:', err);
                }
            }

            try {
                recorder.start();
                setTimeout(() => {
                    if (state.isCapturing && recorder.state === 'recording') {
                        recorder.stop();
                    }
                }, state.chunkInterval);
            } catch (err) {
                console.error('❌ Error starting recorder:', err);
                showError('Audio recording failed: ' + err.message);
                state.isCapturing = false;
            }

            state.mediaRecorder = recorder;
            state.systemStream = stream;
            state.isCapturing = true;

            if (elements.captureBtn) elements.captureBtn.classList.add('active');
            if (elements.statusText) {
                elements.statusText.innerHTML = `
                    <strong>🎙️ Capturing Audio...</strong><br>
                    <span style="font-size: 0.85rem;">Translating to ${getTargetLanguageName()} • 3s chunks</span>
                `;
                elements.statusText.classList.add('capturing');
            }

            updateDebug(`✅ Capture started - Translating to ${getTargetLanguageName()}`);

        } catch (e) {
            if (e.name !== 'NotAllowedError') {
                console.error('❌ Error:', e);
                showError('Capture failed: ' + e.message);
            }
        }
    }

    function stopCapture() {
        finalizeCurrentBubble();
        
        if (state.mediaRecorder) {
            state.mediaRecorder.stop();
        }
        if (state.systemStream) {
            state.systemStream.getTracks().forEach(track => track.stop());
        }
        state.isCapturing = false;

        if (elements.captureBtn) elements.captureBtn.classList.remove('active');
        if (elements.statusText) {
            elements.statusText.innerHTML = 'Click to start capturing audio';
            elements.statusText.classList.remove('capturing');
        }

        updateDebug('🛑 Capture stopped');
    }

    function clearAll() {
        if (!confirm('Clear all translations?')) return;
        
        finalizeCurrentBubble();
        if (elements.translationsContainer) {
            elements.translationsContainer.innerHTML = '';
            elements.translationsContainer.appendChild(elements.emptyState);
        }
        if (elements.emptyState) elements.emptyState.style.display = 'flex';
        
        state.totalTranslations = 0;
        state.totalWords = 0;
        state.languageCounts = { hindi: 0, hinglish: 0, english: 0 };
        state.transcribeTimes = [];
        updateCount();
        updateLanguageBreakdown();
        if (elements.wordCount) elements.wordCount.textContent = '0';
        if (elements.processingTime) elements.processingTime.textContent = '0.0s';
        
        sendMessage({ type: 'clear_session' });
        updateDebug('Cleared all translations');
    }

    function updateDebug(message) {
        if (!elements.debugInfo) return;
        const timestamp = new Date().toLocaleTimeString();
        elements.debugInfo.textContent = `[${timestamp}] ${message}`;
    }

    function showError(message) {
        const toast = document.createElement('div');
        toast.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #f56565;
            color: white;
            padding: 16px 24px;
            border-radius: 12px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.3);
            z-index: 10000;
            font-weight: 600;
        `;
        toast.innerHTML = `<i class="fas fa-exclamation-circle"></i> ${message}`;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 4000);
    }

    // Initialize
    connectWebSocket();
    updateDebug('Ready • Speaker detection enabled');
    console.log('✅ Multi-speaker translator loaded (Django Channels)');
});
