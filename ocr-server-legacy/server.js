// server.js - V5.7 Mobile Optimized (Width 1400, Threshold 170)
import express from 'express';
import LensCore from 'chrome-lens-ocr/src/core.js';
import fs from 'node:fs';
import path from 'node:path';
import multer from 'multer';
import fetch from 'node-fetch';
import { program } from 'commander';
import sharp from 'sharp';

const app = express();

// --- Command-line Argument Parsing ---
program
    .option('--ip <string>', 'Specify the server IP address to bind to', '127.0.0.1')
    .option('--port <number>', 'Specify the server port to listen on', 3000)
    .option('--cache-path <string>', 'Specify a custom path for the cache file', process.cwd())
    .option('--no-preprocess', 'Disable image preprocessing (upscaling and binarization)')
    // UPDATED: Changed from target-height to target-width
    .option('--target-width <number>', 'Target width for upscaling (default: 1400)', 1400)
    .option('--threshold <number>', 'Binarization threshold 0-255 (default: 170)', 170)
    .parse(process.argv);

const options = program.opts();
const host = options.ip;
const port = options.port;
const customCachePath = path.resolve(options.cachePath);
const enablePreprocessing = options.preprocess !== false;
// UPDATED: Use targetWidth
const targetWidth = parseInt(options.targetWidth);
const binarizeThreshold = parseInt(options.threshold);

const lens = new LensCore();
const CACHE_FILE_PATH = path.join(customCachePath, 'ocr-cache.json');
const upload = multer({ dest: 'uploads/' });
let ocrCache = new Map();
let ocrRequestsProcessed = 0;
let activeJobCount = 0;

// --- Auto-Merge Configuration ---
const AUTO_MERGE_CONFIG = {
    enabled: true,
    dist_k: 1.2,
    font_ratio: 1.3,
    perp_tol: 0.5,
    overlap_min: 0.1,
    min_line_ratio: 0.5,
    font_ratio_for_mixed: 1.1,
    mixed_min_overlap_ratio: 0.5,
    add_space_on_merge: false,
};

// --- Image Preprocessing Functions ---

/**
 * Optimized Preprocessing for Mobile:
 * 1. Resizes to 1400px WIDTH (fast/light).
 * 2. Applies Threshold 170 (high contrast).
 * 3. Compresses to WebP Q75 (tiny payload).
 */
async function preprocessImage(imageBuffer, targetWidth, threshold) {
    let pipeline = sharp(imageBuffer);
    const metadata = await pipeline.metadata();
    
    let finalWidth = metadata.width;
    let finalHeight = metadata.height;
    
    // Step 1: Resize based on Width (Lanczos2 for speed/quality balance)
    if (metadata.width !== targetWidth) {
        const scaleFactor = targetWidth / metadata.width;
        finalWidth = targetWidth;
        finalHeight = Math.round(metadata.height * scaleFactor);
        
        pipeline = pipeline.resize(finalWidth, finalHeight, {
            kernel: 'lanczos2',
            fit: 'fill'
        });
    }
    
    // Step 2: Binarize -> Compress to WebP
    console.log(`[Preprocess] Resize to ${finalWidth}px Width (${finalHeight}px Height) -> Threshold ${threshold} -> WebP (Q75)`);
    
    pipeline = pipeline
        .greyscale()
        .normalise()
        .threshold(threshold)
        .webp({ quality: 75 }); 
    
    const buffer = await pipeline.toBuffer();
    
    return {
        buffer,
        width: finalWidth,
        height: finalHeight
    };
}

// --- Auto-Merge Logic (Standard) ---
class UnionFind {
    constructor(size) {
        this.parent = Array.from({ length: size }, (_, i) => i);
        this.rank = Array(size).fill(0);
    }
    find(i) {
        if (this.parent[i] === i) return i;
        return this.parent[i] = this.find(this.parent[i]);
    }
    union(i, j) {
        const rootI = this.find(i);
        const rootJ = this.find(j);
        if (rootI !== rootJ) {
            if (this.rank[rootI] > this.rank[rootJ]) this.parent[rootJ] = rootI;
            else if (this.rank[rootI] < this.rank[rootJ]) this.parent[rootI] = rootJ;
            else { this.parent[rootJ] = rootI; this.rank[rootI]++; }
            return true;
        }
        return false;
    }
}
function median(data) {
    if (!data || data.length === 0) return 0;
    const sorted = [...data].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}
function _groupOcrData(lines, naturalWidth, naturalHeight, config) {
    if (!lines || lines.length < 2 || !naturalWidth || !naturalHeight) return lines.map(line => [line]);
    const CHUNK_MAX_HEIGHT = 3000;
    const normScale = 1000 / naturalWidth;
    const processedLines = lines.map((line, index) => {
        const bbox = line.tightBoundingBox;
        const normalizedBbox = {
            x: (bbox.x * naturalWidth) * normScale,
            y: (bbox.y * naturalHeight) * normScale,
            width: (bbox.width * naturalWidth) * normScale,
            height: (bbox.height * naturalHeight) * normScale,
        };
        normalizedBbox.right = normalizedBbox.x + normalizedBbox.width;
        normalizedBbox.bottom = normalizedBbox.y + normalizedBbox.height;
        const isVertical = normalizedBbox.width <= normalizedBbox.height;
        return {
            originalIndex: index, isVertical,
            fontSize: isVertical ? normalizedBbox.width : normalizedBbox.height,
            bbox: normalizedBbox,
            pixelTop: bbox.y * naturalHeight,
            pixelBottom: (bbox.y + bbox.height) * naturalHeight,
        };
    });
    processedLines.sort((a, b) => a.pixelTop - b.pixelTop);
    const allGroups = [];
    let currentLineIndex = 0;
    while (currentLineIndex < processedLines.length) {
        let chunkStartIndex = currentLineIndex;
        let chunkEndIndex = processedLines.length - 1;
        if (naturalHeight > CHUNK_MAX_HEIGHT) {
            const chunkTopY = processedLines[chunkStartIndex].pixelTop;
            for (let i = chunkStartIndex + 1; i < processedLines.length; i++) {
                if ((processedLines[i].pixelBottom - chunkTopY) <= CHUNK_MAX_HEIGHT) chunkEndIndex = i;
                else break;
            }
        }
        const chunkLines = processedLines.slice(chunkStartIndex, chunkEndIndex + 1);
        const uf = new UnionFind(chunkLines.length);
        const horizontalLines = chunkLines.filter(l => !l.isVertical);
        const verticalLines = chunkLines.filter(l => l.isVertical);
        const initialMedianH = median(horizontalLines.map(l => l.bbox.height));
        const initialMedianW = median(verticalLines.map(l => l.bbox.width));
        const primaryH = horizontalLines.filter(l => l.bbox.height >= initialMedianH * config.min_line_ratio);
        const primaryV = verticalLines.filter(l => l.bbox.width >= initialMedianW * config.min_line_ratio);
        const robustMedianH = median(primaryH.map(l => l.bbox.height)) || initialMedianH || 20;
        const robustMedianW = median(primaryV.map(l => l.bbox.width)) || initialMedianW || 20;
        for (let i = 0; i < chunkLines.length; i++) {
            for (let j = i + 1; j < chunkLines.length; j++) {
                const lineA = chunkLines[i], lineB = chunkLines[j];
                if (lineA.isVertical !== lineB.isVertical) continue;
                const medianForOrientation = lineA.isVertical ? robustMedianW : robustMedianH;
                const isAPrimary = lineA.fontSize >= medianForOrientation * config.min_line_ratio;
                const isBPrimary = lineB.fontSize >= medianForOrientation * config.min_line_ratio;
                let fontRatioThreshold = config.font_ratio;
                if (isAPrimary !== isBPrimary) fontRatioThreshold = config.font_ratio_for_mixed;
                const fontRatio = Math.max(lineA.fontSize / lineB.fontSize, lineB.fontSize / lineA.fontSize);
                if (fontRatio > fontRatioThreshold) continue;
                const distThreshold = medianForOrientation * config.dist_k;
                let readingGap, perpOverlap;
                if (lineA.isVertical) {
                    readingGap = Math.max(0, Math.max(lineA.bbox.x, lineB.bbox.x) - Math.min(lineA.bbox.right, lineB.bbox.right));
                    perpOverlap = Math.max(0, Math.min(lineA.bbox.bottom, lineB.bbox.bottom) - Math.max(lineA.bbox.y, lineB.bbox.y));
                } else {
                    readingGap = Math.max(0, Math.max(lineA.bbox.y, lineB.bbox.y) - Math.min(lineA.bbox.bottom, lineB.bbox.bottom));
                    perpOverlap = Math.max(0, Math.min(lineA.bbox.right, lineB.bbox.right) - Math.max(lineA.bbox.x, lineB.bbox.x));
                }
                if (readingGap > distThreshold) continue;
                const smallerPerpSize = Math.min(lineA.isVertical ? lineA.bbox.height : lineA.bbox.width, lineB.isVertical ? lineB.bbox.height : lineB.bbox.width);
                if (smallerPerpSize > 0 && perpOverlap / smallerPerpSize < config.overlap_min) continue;
                if (isAPrimary !== isBPrimary && smallerPerpSize > 0 && (perpOverlap / smallerPerpSize < config.mixed_min_overlap_ratio)) continue;
                uf.union(i, j);
            }
        }
        const tempGroups = {};
        for (let i = 0; i < chunkLines.length; i++) {
            const root = uf.find(i);
            if (!tempGroups[root]) tempGroups[root] = [];
            tempGroups[root].push(chunkLines[i]);
        }
        for (const rootId in tempGroups) allGroups.push(tempGroups[rootId].map(pLine => lines[pLine.originalIndex]));
        currentLineIndex = chunkEndIndex + 1;
    }
    return allGroups;
}
function autoMergeOcrData(lines, naturalWidth, naturalHeight, config) {
    if (!config.enabled || !lines || lines.length < 2) return lines;
    const groups = _groupOcrData(lines, naturalWidth, naturalHeight, config);
    const finalMergedData = [];
    for (const group of groups) {
        if (group.length === 1) { finalMergedData.push(group[0]); continue; }
        const verticalCount = group.filter(l => l.tightBoundingBox.height > l.tightBoundingBox.width).length;
        const isVerticalGroup = verticalCount > group.length / 2;
        group.sort((a, b) => {
            const boxA = a.tightBoundingBox;
            const boxB = b.tightBoundingBox;
            if (isVerticalGroup) {
                const centerXA = boxA.x + boxA.width / 2;
                const centerXB = boxB.x + boxB.width / 2;
                if (centerXA !== centerXB) return centerXB - centerXA;
                return (boxA.y + boxA.height / 2) - (boxB.y + boxB.height / 2);
            } else {
                const centerYA = boxA.y + boxA.height / 2;
                const centerYB = boxB.y + boxB.height / 2;
                if (centerYA !== centerYB) return centerYA - centerYB;
                return (boxA.x + boxA.width / 2) - (boxB.x + boxB.width / 2);
            }
        });
        const joinChar = config.add_space_on_merge ? ' ' : '\u200B';
        const combinedText = group.map(l => l.text).join(joinChar);
        const minX = Math.min(...group.map(l => l.tightBoundingBox.x));
        const minY = Math.min(...group.map(l => l.tightBoundingBox.y));
        const maxR = Math.max(...group.map(l => l.tightBoundingBox.x + l.tightBoundingBox.width));
        const maxB = Math.max(...group.map(l => l.tightBoundingBox.y + l.tightBoundingBox.height));
        finalMergedData.push({
            text: combinedText, isMerged: true, forcedOrientation: isVerticalGroup ? 'vertical' : 'horizontal',
            tightBoundingBox: { x: minX, y: minY, width: maxR - minX, height: maxB - minY }
        });
    }
    return finalMergedData;
}

// --- Utility Functions ---

function loadCacheFromFile() {
    try {
        if (fs.existsSync(CACHE_FILE_PATH)) {
            const fileContent = fs.readFileSync(CACHE_FILE_PATH, 'utf-8');
            const data = JSON.parse(fileContent);
            ocrCache = new Map(Object.entries(data));
            console.log(`[Cache] Loaded ${ocrCache.size} items from ${CACHE_FILE_PATH}`);
        } else console.log(`[Cache] No cache file found.`);
    } catch (error) { console.error('[Cache] Error loading cache:', error); }
}

function saveCacheToFile() {
    try {
        const cacheDir = path.dirname(CACHE_FILE_PATH);
        if (!fs.existsSync(cacheDir)) fs.mkdirSync(cacheDir, { recursive: true });
        const data = Object.fromEntries(ocrCache);
        fs.writeFileSync(CACHE_FILE_PATH, JSON.stringify(data, null, 2));
    } catch (error) { console.error('[Cache] Error saving cache:', error); }
}

function transformOcrData(lensResult) {
    if (!lensResult?.segments) return [];
    return lensResult.segments.map(({ text, boundingBox }) => ({
        text: text,
        tightBoundingBox: {
            x: boundingBox.centerPerX - (boundingBox.perWidth / 2), 
            y: boundingBox.centerPerY - (boundingBox.perHeight / 2),
            width: boundingBox.perWidth, 
            height: boundingBox.perHeight,
        }
    }));
}

// --- Background Job ---
async function runChapterProcessingJob(baseUrl, authUser, authPass, context) {
    activeJobCount++;
    console.log(`[JobRunner] [${context}] Started job for ...${baseUrl.slice(-40)}.`);
    let pageIndex = 0;
    let consecutiveErrors = 0;
    const SERVER_URL_BASE = `http://${host}:${port}`;
    while (consecutiveErrors < 3) {
        const imageUrl = `${baseUrl}${pageIndex}`;
        if (ocrCache.has(imageUrl)) { pageIndex++; consecutiveErrors = 0; continue; }
        const encodedUrl = encodeURIComponent(imageUrl);
        const encodedContext = encodeURIComponent(context);
        let targetUrl = `${SERVER_URL_BASE}/ocr?url=${encodedUrl}&context=${encodedContext}`;
        if (authUser) targetUrl += `&user=${authUser}&pass=${authPass || ''}`;
        try {
            const response = await fetch(targetUrl, { timeout: 45000 });
            if (response.ok) consecutiveErrors = 0;
            else { consecutiveErrors++; if (response.status === 404) break; }
        } catch (e) { consecutiveErrors++; }
        pageIndex++;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    activeJobCount--;
}

// --- Middleware & Endpoints ---

app.use(express.json());
app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept');
    next();
});

app.get('/', (req, res) => {
    res.json({
        status: 'running', 
        requests_processed: ocrRequestsProcessed,
        items_in_cache: ocrCache.size, 
        active_preprocess_jobs: activeJobCount, 
        preprocessing: { enabled: enablePreprocessing, width: targetWidth, format: 'WebP Q75' }
    });
});

app.get('/ocr', async (req, res) => {
    const { url: imageUrl, user: authUser, pass: authPass, context = "No Context" } = req.query;
    if (!imageUrl) return res.status(400).json({ error: 'Image URL is required' });

    if (ocrCache.has(imageUrl)) {
        const cachedEntry = ocrCache.get(imageUrl);
        return res.json(cachedEntry.data !== undefined ? cachedEntry.data : cachedEntry);
    }
    
    console.log(`[OCR] [${context}] Processing: ${imageUrl}`);
    try {
        const fetchOptions = {};
        if (authUser) {
            const auth = 'Basic ' + Buffer.from(`${authUser}:${authPass || ''}`).toString('base64');
            fetchOptions.headers = { 'Authorization': auth };
        }

        const response = await fetch(imageUrl, fetchOptions);
        if (!response.ok) throw new Error(`Failed to download: ${response.status}`);
        let imageBuffer = Buffer.from(await response.arrayBuffer());

        let fullWidth, fullHeight;
        if (enablePreprocessing) {
            // UPDATED: Pass targetWidth instead of height
            const preprocessed = await preprocessImage(imageBuffer, targetWidth, binarizeThreshold);
            imageBuffer = preprocessed.buffer;
            fullWidth = preprocessed.width;
            fullHeight = preprocessed.height;
        } else {
            const image = sharp(imageBuffer);
            const metadata = await image.metadata();
            fullWidth = metadata.width;
            fullHeight = metadata.height;
        }

        let allFinalResults = [];
        const MAX_CHUNK_HEIGHT = 4000;

        // Note: Even with Width resizing, if the resulting image is still extremely tall 
        // (like a webtoon strip > 4000px height), this logic ensures it is chunked.
        if (fullHeight > MAX_CHUNK_HEIGHT) {
            console.log(`[OCR] [${context}] Tall image (${fullHeight}px). Chunking.`);
            const image = sharp(imageBuffer);
            
            for (let yOffset = 0; yOffset < fullHeight; yOffset += MAX_CHUNK_HEIGHT) {
                const currentTop = Math.round(yOffset);
                if (currentTop >= fullHeight) continue;
                let chunkHeight = Math.min(MAX_CHUNK_HEIGHT, fullHeight - currentTop);
                if (currentTop + chunkHeight > fullHeight) chunkHeight = fullHeight - currentTop;
                if (chunkHeight <= 0) continue;

                const chunkBuffer = await image.clone().extract({ 
                    left: 0, 
                    top: currentTop, 
                    width: fullWidth, 
                    height: chunkHeight 
                })
                .webp({ quality: 75 })
                .toBuffer();
                
                const dataUrl = `data:image/webp;base64,${chunkBuffer.toString('base64')}`;

                console.log(`[OCR] [${context}] Sending chunk y=${currentTop} (WebP)`);
                const rawChunkResults = transformOcrData(await lens.scanByURL(dataUrl));
                
                let mergedChunkResults = rawChunkResults;
                if (AUTO_MERGE_CONFIG.enabled && rawChunkResults.length > 0) {
                    mergedChunkResults = autoMergeOcrData(rawChunkResults, fullWidth, chunkHeight, AUTO_MERGE_CONFIG);
                }

                mergedChunkResults.forEach(result => {
                    const bbox = result.tightBoundingBox;
                    const yGlobalPx = (bbox.y * chunkHeight) + currentTop;
                    bbox.y = yGlobalPx / fullHeight;
                    bbox.height = (bbox.height * chunkHeight) / fullHeight;
                    allFinalResults.push(result);
                });
            }
        } else {
            // Standard path
            const mimeType = enablePreprocessing ? 'image/webp' : 'image/png';
            const dataUrl = `data:${mimeType};base64,${imageBuffer.toString('base64')}`;
            
            const rawResults = transformOcrData(await lens.scanByURL(dataUrl));
            allFinalResults = rawResults;
            if (AUTO_MERGE_CONFIG.enabled && rawResults.length > 0) {
                allFinalResults = autoMergeOcrData(rawResults, fullWidth, fullHeight, AUTO_MERGE_CONFIG);
            }
        }
        
        ocrRequestsProcessed++;
        ocrCache.set(imageUrl, { context, data: allFinalResults });
        saveCacheToFile();
        
        console.log(`[OCR] [${context}] Success! Saved ${allFinalResults.length} text segments to cache.`);
        res.json(allFinalResults);

    } catch (error) {
        console.error(`[OCR] [${context}] Failed:`, error.message);
        res.status(500).json({ error: `OCR process failed: ${error.message}` });
    }
});

app.post("/preprocess-chapter", (req, res) => {
    const { baseUrl, user, pass, context = "No Context" } = req.body;
    if (!baseUrl) return res.status(400).json({ error: "baseUrl is required" });
    runChapterProcessingJob(baseUrl, user, pass, context);
    return res.status(202).json({ status: "accepted" });
});

app.post("/purge-cache", (req, res) => {
    ocrCache.clear();
    saveCacheToFile();
    res.json({ status: "success" });
});

app.get('/export-cache', (req, res) => {
    if (fs.existsSync(CACHE_FILE_PATH)) res.download(CACHE_FILE_PATH, 'ocr-cache.json');
    else res.status(404).json({ error: 'No cache file.' });
});

app.post('/import-cache', upload.single('cacheFile'), (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'No file.' });
    try {
        const fileContent = fs.readFileSync(req.file.path, 'utf-8');
        const importedData = JSON.parse(fileContent);
        for (const [key, value] of Object.entries(importedData)) {
            if (!ocrCache.has(key)) ocrCache.set(key, (value && value.data) ? value : { context: "Import", data: value });
        }
        saveCacheToFile();
        fs.unlinkSync(req.file.path);
        res.json({ message: `Import successful.`, total: ocrCache.size });
    } catch (error) { res.status(500).json({ error: error.message }); }
});

app.listen(port, host, (err) => {
    if (err) console.error('Error:', err);
    else {
        loadCacheFromFile();
        console.log(`Local OCR Server V5.7 (Mobile Optimized) listening at http://${host}:${port}`);
        if (enablePreprocessing) console.log(`Preprocessing: WebP (Q75) Output @ Width ${targetWidth}px | Threshold ${binarizeThreshold}`);
    }
});
