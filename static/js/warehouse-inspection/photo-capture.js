/**
 * Checkpoint proof-photo capture.
 *
 * LIVE CAMERA ONLY - by design there is no file input anywhere in this flow.
 * The module's rule is that proof photos must be captured live (no gallery
 * pick, no clipboard paste, no file import), and `<input type="file"
 * capture="environment">` cannot enforce that: `capture` is only a hint that
 * desktop browsers ignore outright (they show a file picker), and several
 * mobile browsers still offer a gallery chooser alongside the camera. So the
 * camera is opened through getUserMedia() instead - a webcam on a laptop, the
 * rear camera on a phone - and a frame is grabbed straight off the video
 * stream into a JPEG. Nothing on disk is ever readable by this flow.
 *
 * getUserMedia requires a secure context: HTTPS, or localhost. Reaching the
 * app over plain http on a LAN IP will fail with a clear message rather than
 * silently falling back to a file picker.
 */
const WI_PHOTO_MAX_BYTES = 5 * 1024 * 1024; // 5 MB
const WI_PHOTO_MIN_BYTES = 400 * 1024; // 400 KB
const WI_PHOTO_UPLOAD_URL = '/warehouse-inspection/api/checkpoint-photos/';

let wiCameraStream = null;
let wiCameraFacing = 'environment';
let wiCameraSettle = null; // { resolve, reject } for the in-flight capture
let wiCameraBlob = null;

function wiGetCsrfToken() {
    const csrfInput = document.querySelector('[name=csrfmiddlewaretoken]');
    if (csrfInput) return csrfInput.value;
    const cookie = document.cookie.split('; ').find(row => row.startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1] : '';
}

function wiCanvasToBlob(canvas, quality) {
    return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
}

function wiCameraEls() {
    return {
        overlay: document.getElementById('wiCameraOverlay'),
        video: document.getElementById('wiCameraVideo'),
        shot: document.getElementById('wiCameraShot'),
        status: document.getElementById('wiCameraStatus'),
        shutter: document.getElementById('wiCameraShutter'),
        retake: document.getElementById('wiCameraRetake'),
        use: document.getElementById('wiCameraUse'),
        close: document.getElementById('wiCameraClose'),
        flip: document.getElementById('wiCameraFlip'),
    };
}

function wiCameraSetStatus(message, isError = false) {
    const { status } = wiCameraEls();
    if (!status) return;
    status.textContent = message || '';
    status.classList.toggle('is-error', Boolean(isError));
}

/**
 * Grab the current video frame as a JPEG inside the server-enforced
 * [400KB, 5MB] range.
 *
 * The 400KB FLOOR is the awkward end here, not the ceiling: a modest laptop
 * webcam (often 640x480 or 1280x720) encodes well under 400KB even at
 * quality 1.0, so a straight capture would be rejected by the server as
 * "too small/low-quality". Quality is therefore pushed to its maximum first
 * and the frame is then scaled UP until it clears the floor, rather than
 * failing a perfectly good photo taken on a low-resolution camera.
 */
async function wiFrameToBlob(video) {
    const nativeWidth = video.videoWidth;
    const nativeHeight = video.videoHeight;
    if (!nativeWidth || !nativeHeight) {
        throw new Error('The camera is still starting up. Please wait a moment and press Capture again.');
    }

    let scale = 1;
    let quality = 0.95;
    let blob = null;

    for (let attempt = 0; attempt < 12; attempt += 1) {
        const width = Math.max(1, Math.round(nativeWidth * scale));
        const height = Math.max(1, Math.round(nativeHeight * scale));
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(video, 0, 0, width, height);

        blob = await wiCanvasToBlob(canvas, quality);
        if (!blob) {
            throw new Error('Could not capture the photo. Please try again.');
        }
        if (blob.size > WI_PHOTO_MAX_BYTES) {
            if (quality > 0.5) {
                quality -= 0.12;
            } else {
                scale *= 0.85;
            }
            continue;
        }
        if (blob.size < WI_PHOTO_MIN_BYTES) {
            if (quality < 1) {
                quality = 1;
            } else {
                scale *= 1.3;
            }
            continue;
        }
        return blob;
    }

    if (blob && blob.size > WI_PHOTO_MAX_BYTES) {
        throw new Error('This photo is too large even after compression. Please retake at a lower camera resolution.');
    }
    throw new Error('Could not produce a photo of an acceptable size (400KB-5MB). Please retake with better lighting.');
}

function wiStopCameraStream() {
    if (!wiCameraStream) return;
    wiCameraStream.getTracks().forEach(track => track.stop());
    wiCameraStream = null;
}

async function wiStartCameraStream() {
    wiStopCameraStream();
    const { video } = wiCameraEls();
    wiCameraStream = await navigator.mediaDevices.getUserMedia({
        video: {
            facingMode: wiCameraFacing,
            // Ask for a high resolution so the JPEG comfortably clears the
            // 400KB server minimum; browsers downgrade to whatever the
            // device actually supports.
            width: { ideal: 2560 },
            height: { ideal: 1440 },
        },
        audio: false,
    });
    video.srcObject = wiCameraStream;
    await video.play();
}

function wiCameraShowLive() {
    const els = wiCameraEls();
    wiCameraBlob = null;
    if (els.shot.src) {
        URL.revokeObjectURL(els.shot.src);
        els.shot.removeAttribute('src');
    }
    els.shot.style.display = 'none';
    els.video.style.display = 'block';
    els.shutter.style.display = 'inline-flex';
    els.retake.style.display = 'none';
    els.use.style.display = 'none';
    wiCameraSetStatus('Point the camera at the issue, then press Capture.');
}

function wiCameraShowShot(blob) {
    const els = wiCameraEls();
    wiCameraBlob = blob;
    els.shot.src = URL.createObjectURL(blob);
    els.shot.style.display = 'block';
    els.video.style.display = 'none';
    els.shutter.style.display = 'none';
    els.retake.style.display = 'inline-flex';
    els.use.style.display = 'inline-flex';
    wiCameraSetStatus(`Captured (${Math.round(blob.size / 1024)} KB). Use this photo, or retake it.`);
}

function wiCloseCamera(result) {
    wiStopCameraStream();
    const els = wiCameraEls();
    if (els.shot && els.shot.src) {
        URL.revokeObjectURL(els.shot.src);
        els.shot.removeAttribute('src');
    }
    if (els.overlay) els.overlay.classList.remove('is-open');
    const settle = wiCameraSettle;
    wiCameraSettle = null;
    wiCameraBlob = null;
    if (settle) settle.resolve(result || null);
}

/**
 * Opens the live camera and resolves with a JPEG Blob once the user accepts a
 * shot, or with null if they close the camera without taking one.
 */
async function openCameraCapture() {
    if (!window.isSecureContext) {
        throw new Error(
            'The camera needs a secure connection. Open this page over HTTPS (or localhost) and try again.'
        );
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('This browser does not support live camera capture. Please use Chrome, Edge or Safari.');
    }

    const els = wiCameraEls();
    if (!els.overlay) {
        throw new Error('The camera dialog is unavailable on this page.');
    }

    els.overlay.classList.add('is-open');
    wiCameraShowLive();
    wiCameraSetStatus('Starting camera...');

    try {
        await wiStartCameraStream();
    } catch (error) {
        els.overlay.classList.remove('is-open');
        wiStopCameraStream();
        if (error && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
            throw new Error('Camera permission was blocked. Allow camera access for this site, then try again.');
        }
        if (error && error.name === 'NotFoundError') {
            throw new Error('No camera was found on this device.');
        }
        if (error && error.name === 'NotReadableError') {
            throw new Error('The camera is already in use by another app. Close it and try again.');
        }
        throw new Error(`Could not start the camera: ${error && error.message ? error.message : error}`);
    }

    wiCameraShowLive();
    return new Promise(resolve => {
        wiCameraSettle = { resolve };
    });
}

function wiWireCameraDialog() {
    const els = wiCameraEls();
    if (!els.overlay) return;

    els.shutter.addEventListener('click', async () => {
        els.shutter.disabled = true;
        wiCameraSetStatus('Capturing...');
        try {
            const blob = await wiFrameToBlob(els.video);
            wiCameraShowShot(blob);
        } catch (error) {
            wiCameraSetStatus(error.message, true);
        } finally {
            els.shutter.disabled = false;
        }
    });

    els.retake.addEventListener('click', () => {
        wiCameraShowLive();
    });

    els.use.addEventListener('click', () => {
        if (!wiCameraBlob) return;
        // Hand the blob over BEFORE wiCloseCamera() clears it.
        const blob = wiCameraBlob;
        wiCloseCamera(blob);
    });

    els.close.addEventListener('click', () => wiCloseCamera(null));

    els.overlay.addEventListener('click', event => {
        if (event.target === els.overlay) wiCloseCamera(null);
    });

    els.flip.addEventListener('click', async () => {
        wiCameraFacing = wiCameraFacing === 'environment' ? 'user' : 'environment';
        wiCameraSetStatus('Switching camera...');
        try {
            await wiStartCameraStream();
            wiCameraShowLive();
        } catch (error) {
            wiCameraSetStatus(`Could not switch camera: ${error.message}`, true);
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && els.overlay.classList.contains('is-open')) {
            wiCloseCamera(null);
        }
    });
}

document.addEventListener('DOMContentLoaded', wiWireCameraDialog);
// Release the camera if the page is being unloaded mid-capture.
window.addEventListener('pagehide', wiStopCameraStream);

/**
 * Uploads a captured photo blob for one checkpoint and returns
 * { upload_token, url } to be attached to that checkpoint's response
 * when the checklist is finally submitted.
 */
async function uploadCheckpointPhoto(checkpointId, blob) {
    const formData = new FormData();
    formData.append('checkpoint', String(checkpointId));
    formData.append('photo', blob, 'proof.jpg');

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);

    let response;
    try {
        response = await fetch(WI_PHOTO_UPLOAD_URL, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': wiGetCsrfToken(),
            },
            body: formData,
            signal: controller.signal,
        });
    } catch (error) {
        if (error.name === 'AbortError') {
            throw new Error('Photo upload timed out. Please check your connection and try again.');
        }
        throw new Error('Photo upload failed. Please check your connection and try again.');
    } finally {
        clearTimeout(timeout);
    }

    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('application/json') ? await response.json().catch(() => ({})) : {};

    if (!response.ok) {
        const message = payload && (payload.detail || payload.photo || payload.checkpoint || payload.error);
        throw new Error(Array.isArray(message) ? message.join(' ') : (message || 'Photo upload failed. Please try again.'));
    }

    return payload;
}
