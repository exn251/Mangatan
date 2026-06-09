import asyncio
import gc  # Added for memory unloading
import os
import re
import threading
import time  # Added for the inactivity monitor
from abc import ABC, abstractmethod
from math import pi
from typing import TypedDict

# Third-Party Imports
import chrome_lens_py
import cv2
import numpy as np
import onnxruntime as ort
import PIL.Image as PILImage
from chrome_lens_py.utils.lens_betterproto import LensOverlayObjectsResponse
from PIL import Image, ImageFilter  # Added ImageFilter for sharpening options
from huggingface_hub import hf_hub_download  # Official download utility

class BoundingBox(TypedDict):
    x: float
    y: float
    width: float
    height: float


class Bubble(TypedDict):
    text: str
    tightBoundingBox: BoundingBox
    orientation: float
    font_size: float
    confidence: float


class Engine(ABC):
    """Base class for OCR engines. Each engine should implement the `ocr` method
    which processes an image and returns a list of Bubble objects.
    """

    @abstractmethod
    async def ocr(self, img: Image) -> list[Bubble]:
        pass


class OneOCR(Engine):
    def __init__(self):
        try:
            import oneocr

            self.engine = oneocr.OcrEngine()
        except ImportError as e:
            print(f"[Warning] OneOCR import failed: {e}")
        except Exception as e:
            print(
                f"[Warning] If you get this error please spam the Mangatan thread: {e}"
            )

        # The height of each chunk to process.
        # A value between 1000-2000 is a good starting point.
        self.CHUNK_HEIGHT = 1500
        # The pixel overlap between chunks to prevent cutting text in half.
        self.OVERLAP = 150

    async def ocr(self, img):
        chunk_image = self.process_image(img)
        # print(json.dumps(chunk_image, indent=2, ensure_ascii=False))
        return chunk_image

    def process_image(self, img: Image) -> list[Bubble]:
        full_width, full_height = img.size
        y_offset = 0
        all_transformed_results: list[Bubble] = []

        while y_offset < full_height:
            # Define the crop box for the current chunk
            box = (
                0,
                y_offset,
                full_width,
                min(y_offset + self.CHUNK_HEIGHT, full_height),
            )

            # Crop the image to get the current chunk
            chunk_image = img.crop(box)
            chunk_width, chunk_height = chunk_image.size

            # Run OCR on the smaller chunk
            results = self.engine.recognize_pil(chunk_image)
            data = self.transform(results, chunk_image.size)

            # Remap the coordinates of the detected text to be relative to the FULL image
            for item in data:
                bbox = item["tightBoundingBox"]
                # Adjust y and height based on the chunk's position and size
                bbox["y"] = (bbox["y"] * chunk_height + y_offset) / full_height
                bbox["height"] = (bbox["height"] * chunk_height) / full_height
                all_transformed_results.append(item)

            # Move to the next chunk position
            y_offset += self.CHUNK_HEIGHT - self.OVERLAP
        return all_transformed_results

    def transform(self, result, image_size) -> list[Bubble]:
        if not result or not result.get("lines"):
            return []

        image_width, image_height = image_size
        if image_width == 0 or image_height == 0:
            return []

        output_json = []

        for line in result.get("lines", []):
            text = line.get("text", "").strip()
            rect = line.get("bounding_rect")

            if not rect or not text or not line.get("words"):
                continue

            x_coords = [rect["x1"], rect["x2"], rect["x3"], rect["x4"]]
            y_coords = [rect["y1"], rect["y2"], rect["y3"], rect["y4"]]
            x_min = min(x_coords)
            y_min = min(y_coords)
            x_max = max(x_coords)
            y_max = max(y_coords)
            width = x_max - x_min
            height = y_max - y_min
            snapped_angle = 90.0 if height > width else 0.0
            word_count = len(line.get("words", []))
            avg_confidence = (
                sum(word.get("confidence", 0.95) for word in line.get("words", []))
                / word_count
                if word_count > 0
                else 0.95
            )

            bubble = Bubble(
                text=text,
                tightBoundingBox=BoundingBox(
                    x=x_min / image_width,
                    y=y_min / image_height,
                    width=width / image_width,
                    height=height / image_height,
                ),
                orientation=snapped_angle,
                font_size=0.04,
                confidence=avg_confidence,
            )
            output_json.append(bubble)

        return output_json

class OneOCRFurigana(Engine):
    """
    OCR engine wrapper that processes large images in overlapping chunks,
    de‑duplicates results, and filters out furigana (small reading aids).
    Includes comprehensive preprocessing to improve OCR accuracy.
    """

    def __init__(self):
        try:
            import oneocr
            self.engine = oneocr.OcrEngine()
        except ImportError as e:
            print(f"[Warning] OneOCRFurigana import failed: {e}")
        except Exception as e:
            print(
                f"[Warning] If you get this error please spam the Mangatan thread: {e}"
            )
        
        # The height of each chunk to process.
        self.CHUNK_HEIGHT = 4000
        # The pixel overlap between chunks to prevent cutting text in half.
        self.OVERLAP = 300
        # Furigana filtering threshold
        self.HORIZONTAL_FURIGANA_THRESHOLD = 0.70
        # Furigana filter toggle
        self.ENABLE_FURIGANAFILTER = True
        
        # Preprocessing toggles
        self.ENABLE_PREPROCESSING = True
        self.ENABLE_UPSCALING = True
        self.TARGET_WIDTH = 2500  # Targeted width for upscaling
        self.ENABLE_SHARPENING = True

    # --------------------------------------------------------------------- #
    # Enhanced Image preprocessing
    # --------------------------------------------------------------------- #
    def preprocess_image(self, img: Image) -> Image:
        """
        Enhanced preprocessing pipeline for manga OCR:
        1. Upscale to target width (2500px) with Lanczos if smaller
        2. Sharpen with unsharp mask
        """
        if not self.ENABLE_PREPROCESSING:
            return img
        
        try:
            from PIL import ImageFilter
            
            original_img = img
            processed_img = img
            
            # Step 1: Upscale based on WIDTH
            if self.ENABLE_UPSCALING:
                original_width, original_height = processed_img.size
                if original_width < self.TARGET_WIDTH:
                    scale_factor = self.TARGET_WIDTH / original_width
                    new_width = self.TARGET_WIDTH
                    new_height = int(original_height * scale_factor)
                    
                    print(f"[Info] Upscaling image: {original_width}x{original_height} -> {new_width}x{new_height}")
                    
                    # FIXED: Use 'PILImage' (the module alias) for constants
                    resample_filter = getattr(PILImage, 'Resampling', PILImage).LANCZOS
                    processed_img = processed_img.resize(
                        (new_width, new_height), 
                        resample_filter
                    )
            
            # Step 2: Sharpen with unsharp mask
            if self.ENABLE_SHARPENING:
                processed_img = processed_img.filter(
                    ImageFilter.UnsharpMask(
                        radius=1.0, 
                        percent=100, 
                        threshold=5
                    )
                )
            
            return processed_img
            
        except Exception as e:
            print(f"[Warning] Preprocessing failed, using original image: {e}")
            return original_img

    # --------------------------------------------------------------------- #
    # Chunk‑boundary helper
    # --------------------------------------------------------------------- #
    def is_near_chunk_boundary(
        self, bbox: dict, chunk_height: int, y_offset: int, full_height: int
    ) -> bool:
        if y_offset == 0:
            return False

        abs_y = bbox["y"] * full_height
        boundary_threshold = self.OVERLAP * 0.75
        return abs_y < (y_offset + boundary_threshold)

    # --------------------------------------------------------------------- #
    # Public async entry point
    # --------------------------------------------------------------------- #
    async def ocr(self, img):
        return self.process_image(img)
        
    # --------------------------------------------------------------------- #
    # Furigana filtering
    # --------------------------------------------------------------------- #
    def filter_furigana(self, bubbles: list[Bubble], debug=False) -> list[Bubble]:
        if not bubbles or not self.ENABLE_FURIGANAFILTER:
            return bubbles

        horiz = [b for b in bubbles if b["orientation"] == 0.0]
        vert  = [b for b in bubbles if b["orientation"] == 90.0]

        def filter_by_height(items):
            if not items: return items
            heights = sorted([item["tightBoundingBox"]["height"] for item in items])
            median_h = heights[len(heights) // 2] if heights else 0
            if median_h == 0: return items

            filtered = []
            for it in items:
                h = it["tightBoundingBox"]["height"]
                if h >= median_h * self.HORIZONTAL_FURIGANA_THRESHOLD:
                    filtered.append(it)
            return filtered

        def filter_by_width(items):
            if not items: return items

            def _is_kana_only(text):
                if not text.strip(): return False
                letters = [ch for ch in text if ch.isalpha()]
                if not letters: return False
                return all(('\u3040' <= ch <= '\u309f') or ('\u30a0' <= ch <= '\u30ff') for ch in letters)

            def _contains_kanji(text):
                return any('\u4e00' <= ch <= '\u9fff' for ch in text)

            def _has_dialogue_punctuation(text):
                dialogue_marks = {'…', '。', '、', '！', '？', '!', '?', '~', '～', '‥', '・', '‼', '⁉', '⁈', '⁇', '—', '─', '―', '「', '」', '『', '』', '（', '）', '【', '】'}
                return any(mark in text for mark in dialogue_marks)

            kana_only_items = [it for it in items if _is_kana_only(it["text"])]
            kanji_items = [it for it in items if _contains_kanji(it["text"])]
            other_items = [it for it in items if it not in kana_only_items and it not in kanji_items]

            if kanji_items and kana_only_items:
                kanji_widths = sorted([it["tightBoundingBox"]["width"] for it in kanji_items])
                median_kanji_w = kanji_widths[len(kanji_widths) // 2]
                
                # LOWERED: 0.81 -> 0.65 to accommodate thin Kana vs thick Kanji
                furigana_threshold = median_kanji_w * 0.65

                filtered_kana = []
                for it in kana_only_items:
                    w = it["tightBoundingBox"]["width"]
                    text = it["text"]
                    text_len = len(text.strip())

                    if _has_dialogue_punctuation(text):
                        filtered_kana.append(it)
                        continue

                    if text_len >= 6:
                        # CHANGED: Long text is almost certainly dialogue. 
                        # Use a very permissive threshold (50% of Kanji width) instead of the previous strict 90%.
                        if w >= median_kanji_w * 0.50:
                            filtered_kana.append(it)
                        continue

                    eff_threshold = furigana_threshold * (0.90 if text_len == 4 else 0.95 if text_len < 4 else 1.0)
                    if w >= eff_threshold:
                        filtered_kana.append(it)

                return kanji_items + filtered_kana + other_items

            widths = sorted([item["tightBoundingBox"]["width"] for item in items])
            threshold = widths[len(widths) // 5] if len(widths) > 5 else (widths[len(widths)//2] * 0.40 if widths else 0)

            filtered = []
            for it in items:
                if _has_dialogue_punctuation(it["text"]) or it["tightBoundingBox"]["width"] >= threshold:
                    filtered.append(it)
            return filtered

        filtered_horiz = filter_by_height(horiz)
        filtered_vert  = filter_by_width(vert)

        filtered_set = {id(b) for b in filtered_horiz + filtered_vert}
        return [b for b in bubbles if id(b) in filtered_set]

    # --------------------------------------------------------------------- #
    # Chunked image processing
    # --------------------------------------------------------------------- #
    def process_image(self, img: Image, debug: bool = False) -> list[Bubble]:
        img = self.preprocess_image(img)
        full_width, full_height = img.size

        if full_height <= self.CHUNK_HEIGHT:
            results = self.engine.recognize_pil(img)
            data = self.transform(results, img.size)
            return self.filter_furigana(data, debug=debug)

        y_offset = 0
        all_transformed_results: list[Bubble] = []
        seen_texts = set()

        while y_offset < full_height:
            chunk_end = min(y_offset + self.CHUNK_HEIGHT, full_height)
            box = (0, y_offset, full_width, chunk_end)
            chunk_image = img.crop(box)
            chunk_width, chunk_height = chunk_image.size

            results = self.engine.recognize_pil(chunk_image)
            data = self.transform(results, chunk_image.size)

            for item in data:
                bbox = item["tightBoundingBox"]
                bbox["y"] = (bbox["y"] * chunk_height + y_offset) / full_height
                bbox["height"] = (bbox["height"] * chunk_height) / full_height

                if self.is_near_chunk_boundary(bbox, chunk_height, y_offset, full_height):
                    continue

                text_key = (item["text"], round(bbox["x"] * 100), round(bbox["y"] * 100))
                if text_key not in seen_texts:
                    seen_texts.add(text_key)
                    all_transformed_results.append(item)

            y_offset += self.CHUNK_HEIGHT - self.OVERLAP

        return self.filter_furigana(all_transformed_results, debug=debug)

    # --------------------------------------------------------------------- #
    # Result transformation
    # --------------------------------------------------------------------- #
    def transform(self, result, image_size) -> list[Bubble]:
        if not result or not result.get("lines"):
            return []

        image_width, image_height = image_size
        if image_width == 0 or image_height == 0:
            return []

        output_json = []
        for line in result.get("lines", []):
            text = line.get("text", "").strip()
            rect = line.get("bounding_rect")
            if not rect or not text or not line.get("words"):
                continue

            x_coords = [rect["x1"], rect["x2"], rect["x3"], rect["x4"]]
            y_coords = [rect["y1"], rect["y2"], rect["y3"], rect["y4"]]

            x_min, y_min = min(x_coords), min(y_coords)
            x_max, y_max = max(x_coords), max(y_coords)
            width, height = x_max - x_min, y_max - y_min

            bubble = Bubble(
                text=text,
                tightBoundingBox=BoundingBox(
                    x=x_min / image_width,
                    y=y_min / image_height,
                    width=width / image_width,
                    height=height / image_height,
                ),
                orientation=90.0 if height > width else 0.0,
                font_size=0.04,
                confidence=sum(w.get("confidence", 0.95) for w in line["words"]) / len(line["words"]),
            )
            output_json.append(bubble)

        return output_json

class GoogleLens(Engine):
    def __init__(self):
        self.engine = chrome_lens_py.LensAPI()

    async def ocr(self, img):
        result = await self.engine.process_image(
            image_path=img, ocr_language="ja", output_format="lines"
        )
        return self.transform(result)

    def transform(self, result: dict) -> list[Bubble]:
        if not result.get("word_data"):
            return []

        output_json: list[Bubble] = []
        lines: list[dict] = result["line_blocks"]

        for line in lines:
            text: str = line["text"]
            geometry: dict[str, float] = line["geometry"]
            center_x = geometry["center_x"]
            center_y = geometry["center_y"]
            width = geometry["width"]
            height = geometry["height"]

            # example: 6.5; degrees from perfect vertical or horizontal line
            angle_deg = geometry["angle_deg"]

            # 90.0 is a vertical line, 0.0 is horizontal
            snapped_angle = 90.0 if height > width else 0.0

            # example: 90.0 + 6.5 = 96.5, or rotated 6.5 degrees clockwise from vertical
            actual_angle = snapped_angle + angle_deg

            bubble = Bubble(
                text=text.replace("･･･", "…"),
                tightBoundingBox=BoundingBox(
                    x=center_x - width / 2,
                    y=center_y - height / 2,
                    width=width,
                    height=height,
                ),
                orientation=round(actual_angle, 1),
                font_size=0.04,
                confidence=0.98,  # Assuming a default confidence value
            )
            output_json.append(bubble)
            # print(json.dumps(bubble, indent=2, ensure_ascii=False))

        return output_json

    # just in case we want to parse it ourselves
    def raw_transform(self, result: dict) -> list[Bubble]:
        output_json: list[Bubble] = []
        response: LensOverlayObjectsResponse = result["raw_response_objects"]

        for paragraph in response.text.text_layout.paragraphs:
            for line in paragraph.lines:
                line_text = (
                    "".join(
                        word.plain_text + (word.text_separator or "")
                        for word in line.words
                    )
                    .strip()
                    .replace("･･･", "…")
                )
                geometry = line.geometry

                bounding_box = geometry.bounding_box
                center_x = bounding_box.center_x
                center_y = bounding_box.center_y
                width = bounding_box.width
                height = bounding_box.height
                rotation_z = bounding_box.rotation_z

                bubble = Bubble(
                    text=line_text,
                    tightBoundingBox=BoundingBox(
                        x=center_x - width / 2,
                        y=center_y - height / 2,
                        width=width,
                        height=height,
                    ),
                    orientation=round(rotation_z * (180 / pi), 1),
                    font_size=0.04,
                    confidence=0.98,
                )
                output_json.append(bubble)

        return output_json

class MangaOCR(Engine):
    """
    OCR engine that uses Meiki text detection on CPU to find text boxes,
    then runs default Manga OCR on batched detected regions.
    """
    
    def __init__(
        self, 
        model_path: str = "meiki.text.detect.v0.1.960x544.onnx",
        confidence_threshold: float = 0.4,
        force_cpu: bool = True
    ):
        self.detect_width = 960
        self.detect_height = 544
        self.BATCH_SIZE = 2 
        self.confidence_threshold = confidence_threshold
        
        # Concurrency limit to prevent CPU core/thread saturation from parallel requests
        self._ocr_semaphore = threading.Semaphore(1) 

        # Resolve clean base folders
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            script_dir = os.getcwd()
            
        models_dir = os.path.join(script_dir, "models")
        os.makedirs(models_dir, exist_ok=True)

        # 1. Resolve and download Meiki model
        try:
            if os.path.exists(model_path):
                resolved_path = model_path
            else:
                filename = os.path.basename(model_path)
                print(f"[MeikiMangaOCR] Resolving '{filename}' via Hugging Face...")
                
                resolved_path = hf_hub_download(
                    repo_id="rtr46/meiki.text.detect.v0",
                    filename=filename,
                    local_dir=models_dir 
                )
            detect_sess_options = ort.SessionOptions()
            detect_sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            
            self.detection_session = ort.InferenceSession(
                resolved_path, 
                sess_options=detect_sess_options,
                providers=['CPUExecutionProvider']
            )
            print(f"[MeikiMangaOCR] Loaded CPU text detection model: {resolved_path}")
        except Exception as e:
            print(f"[Error] Failed to load Meiki text detection model: {e}")
            raise
        
        # 2. Initialize default manga-ocr
        try:
            from manga_ocr import MangaOcr as MOCR
            import logging
            from loguru import logger
            
            logger.disable('manga_ocr')
            logging.getLogger('transformers').setLevel(logging.ERROR)
            
            # Loads the default model ('kha-white/manga-ocr-base')
            self.manga_ocr = MOCR(force_cpu=force_cpu)
            print(f"[MeikiMangaOCR] Loaded default Manga OCR model "
                  f"(Device: {'CPU' if force_cpu else 'GPU'}, Batch Size: {self.BATCH_SIZE})")
                  
        except ImportError as e:
            print(f"[Error] manga-ocr not installed: {e}")
            raise
        except Exception as e:
            print(f"[Error] Failed to initialize Manga OCR: {e}")
            raise

    def _resize(self, image: np.ndarray, w: int, h: int):
        return cv2.resize(image, (w, h), interpolation=cv2.INTER_LINEAR)

    def _detect_text_boxes(self, image: np.ndarray):
        img_height, img_width = image.shape[:2]
        resized_image = self._resize(image, self.detect_width, self.detect_height)
        
        img_normalized = resized_image.astype(np.float32) / 255.0
        img_transposed = np.transpose(img_normalized, (2, 0, 1))
        image_input_tensor = np.expand_dims(img_transposed, axis=0)
        
        size_tensor = np.array([[img_width, img_height]], dtype=np.int64)
        
        input_names = [inp.name for inp in self.detection_session.get_inputs()]
        inputs = {
            input_names[0]: image_input_tensor,
            input_names[1]: size_tensor
        }
        
        outputs = self.detection_session.run(None, inputs)
        _, boxes, scores = outputs 
        
        boxes = boxes[0]
        scores = scores[0]
        
        original_boxes = []
        for box, score in zip(boxes, scores):
            if score > self.confidence_threshold:
                x_min, y_min, x_max, y_max = box
                
                final_x_min = int(x_min)
                final_y_min = int(y_min)
                final_x_max = int(x_max)
                final_y_max = int(y_max)
                
                final_x_min = max(0, final_x_min + 0)
                final_x_max = min(img_width, final_x_max + 4)
                final_y_min = max(0, final_y_min - 2)
                final_y_max = min(img_height, final_y_max + 4)
                
                if final_x_min < final_x_max and final_y_min < final_y_max:
                    original_boxes.append((final_x_min, final_y_min, final_x_max, final_y_max))
        
        return original_boxes

    async def ocr(self, img: Image) -> list[Bubble]:
        return await asyncio.to_thread(self._ocr_internal, img)

    def _ocr_internal(self, img: Image) -> list[Bubble]:
        img_array = np.array(img)
        original_width, original_height = img.size
        
        boxes = self._detect_text_boxes(img_array)
        
        if not boxes:
            print("[MeikiMangaOCR] No text boxes detected")
            return []
            
        print(f"[MeikiMangaOCR] Detected {len(boxes)} text boxes")
        
        crop_data = []
        for x1, y1, x2, y2 in boxes:
            crop = img.crop((x1, y1, x2, y2))
            if crop.width >= 10 and crop.height >= 10:
                crop = crop.convert("L").convert("RGB")
                crop_data.append((crop, (x1, y1, x2, y2)))
        
        if not crop_data:
            return []

        bubbles = []
        device = self.manga_ocr.model.device
        
        for i in range(0, len(crop_data), self.BATCH_SIZE):
            batch_chunk = crop_data[i : i + self.BATCH_SIZE]
            batch_images = [item[0] for item in batch_chunk]
            batch_boxes = [item[1] for item in batch_chunk]
            
            try:
                # 1. Preprocess images using the standard Image Processor
                pixel_values = self.manga_ocr.processor(
                    batch_images, 
                    return_tensors="pt"
                ).pixel_values.to(device)
                
                # 2. Concurrency limit & 40 token constraint
                with self._ocr_semaphore:
                    generated_ids = self.manga_ocr.model.generate(
                        pixel_values,
                        max_new_tokens=40,
                        num_beams=1,
                    )
                
                # 3. Decode tokens using the tokenizer
                batch_texts = self.manga_ocr.tokenizer.batch_decode(
                    generated_ids, 
                    skip_special_tokens=True
                )
                
                for text, (x1, y1, x2, y2) in zip(batch_texts, batch_boxes):
                    text = re.sub(r'\s+', '', text)
                    if not text:
                        continue
                        
                    width = x2 - x1
                    height = y2 - y1
                    
                    bubbles.append(Bubble(
                        text=text,
                        tightBoundingBox=BoundingBox(
                            x=x1 / original_width,
                            y=y1 / original_height,
                            width=width / original_width,
                            height=height / original_height,
                        ),
                        orientation=90.0 if height > width else 0.0,
                        font_size=0.04,
                        confidence=0.95, 
                    ))
                    
            except Exception as e:
                print(f"[Warning] Batch OCR failed for batch starting at {i}: {e}")
                continue
                
        print(f"[MeikiMangaOCR] Successfully processed {len(bubbles)} text regions")
        return bubbles
        

class MangaOCRDirectML(Engine):
    """
    Combines Meiki text detection (ONNX) with Manga OCR (ONNX via Optimum/Transformers).
    
    Includes auto-unloading mechanism to free resources after 10 minutes of inactivity.
    Automatically downloads all required models into the local 'models' folder.
    """
    def __init__(
        self, 
        model_path: str = "meiki.text.detect.v0.1.960x544.onnx",
        confidence_threshold: float = 0.4,
        pretrained_model_name_or_path: str = "mangaocronnx", 
    ):
        self.ENABLE_PREPROCESSING = False
        self.ENABLE_UPSCALING = False
        self.ENABLE_SHARPENING = False
        self.TARGET_WIDTH = 4000
        self.confidence_threshold = confidence_threshold
        
        # Updated to the new model's direct input size expectations
        self.detect_width = 960
        self.detect_height = 544
        self.BATCH_SIZE = 32 

        # --- Resource Management Init ---
        self._lock = threading.Lock()
        self._gpu_semaphore = threading.Semaphore(1)  # Limits GPU execution concurrency
        self.last_access_time = time.time()
        self.timeout_seconds = 600  # 10 Minutes
        self.models_loaded = False
        
        # Placeholders for models
        self.detection_session = None
        self.processor = None
        self.recognition_model = None

        # Resolve clean base folders
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            script_dir = os.getcwd()
            
        models_dir = os.path.join(script_dir, "models")
        os.makedirs(models_dir, exist_ok=True)

        # 1. Resolve & Download Detection Model (Meiki)
        if os.path.exists(model_path):
            self.detect_model_path = model_path
        else:
            filename = os.path.basename(model_path)
            print(f"[MeikiMangaOCROnnx] Resolving Meiki model '{filename}'...")
            self.detect_model_path = hf_hub_download(
                repo_id="rtr46/meiki.text.detect.v0",
                filename=filename,
                local_dir=models_dir
            )

        # 2. Resolve & Download ONNX OCR Model files
        if os.path.isabs(pretrained_model_name_or_path):
            local_ocr_path = pretrained_model_name_or_path
        else:
            local_ocr_path = os.path.join(models_dir, pretrained_model_name_or_path)
            
        os.makedirs(local_ocr_path, exist_ok=True)

        # Map out files and their respective subfolders in the repo
        required_ocr_files = [
            ("config.json", None),
            ("generation_config.json", None),
            ("preprocessor_config.json", None),
            ("special_tokens_map.json", None),
            ("tokenizer.json", None),
            ("tokenizer_config.json", None),
            ("vocab.txt", None),
            ("encoder_model.onnx", "onnx"),
            ("decoder_model_merged.onnx", "onnx")
        ]

        print(f"[MeikiMangaOCROnnx] Verifying ONNX OCR dependencies in: {local_ocr_path}")
        for file_name, subfolder in required_ocr_files:
            file_path = os.path.join(local_ocr_path, file_name)
            
            if not os.path.exists(file_path):
                print(f"[MeikiMangaOCROnnx] File '{file_name}' not found. Downloading from Hugging Face...")
                if subfolder:
                    # Download from subfolder
                    hf_file_path = f"{subfolder}/{file_name}"
                    downloaded_path = hf_hub_download(
                        repo_id="xingliao/manga-ocr-onnx-full",
                        filename=hf_file_path,
                        local_dir=local_ocr_path
                    )
                    
                    # Inline import to prevent NameError if missing from file header
                    import shutil
                    shutil.move(downloaded_path, file_path)
                    
                    # Cleanup empty 'onnx' subfolder directory
                    try:
                        os.rmdir(os.path.dirname(downloaded_path))
                    except OSError:
                        pass
                else:
                    # Standard root file download
                    hf_hub_download(
                        repo_id="xingliao/manga-ocr-onnx-full",
                        filename=file_name,
                        local_dir=local_ocr_path
                    )

        self.ocr_model_source = local_ocr_path

        # Load models immediately on startup to ensure fast first requests
        self._load_models()

        # Start the background monitor thread
        monitor_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        monitor_thread.start()

    def _load_models(self):
        """Loads the models into memory if they aren't already."""
        if self.models_loaded:
            return

        print(f"[MeikiMangaOCROnnx] Waking up... Loading models.")
        
        # 1. Initialize Meiki Detection Model (CPU ONLY)
        try:
            detect_sess_options = ort.SessionOptions()
            detect_sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self.detection_session = ort.InferenceSession(
                self.detect_model_path, 
                sess_options=detect_sess_options,
                providers=['CPUExecutionProvider']
            )
        except Exception as e:
            print(f"[Error] Failed to load Meiki text detection model: {e}")
            raise

        # 2. Initialize ONNX Manga OCR Model (DirectML)
        try:
            from transformers import TrOCRProcessor
            from optimum.onnxruntime import ORTModelForVision2Seq
            
            sess_options = ort.SessionOptions()
            sess_options.log_severity_level = 3
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            # Loads processor directly with use_fast=True
            self.processor = TrOCRProcessor.from_pretrained(self.ocr_model_source, use_fast=True)
            
            self.recognition_model = ORTModelForVision2Seq.from_pretrained(
                self.ocr_model_source,
                provider="DmlExecutionProvider", 
                #use_cache=False,        # <--- CRITICAL: Enables KV Caching
                use_merged=True,         # <--- FORCE Optimum to recognize the merged file
                use_io_binding=False,    # <--- OPTIMIZATION: Avoids CPU-GPU data transfer overhead
                export=False,
                session_options=sess_options
            )
        except Exception as e:
            print(f"[Error] Failed to initialize ONNX Manga OCR: {e}")
            raise

        self.models_loaded = True
        self.last_access_time = time.time() 
        print(f"[MeikiMangaOCROnnx] Models loaded successfully.")

    def _unload_models(self):
        """Unloads models and forces garbage collection."""
        if not self.models_loaded:
            return
            
        print(f"[MeikiMangaOCROnnx] Standby timeout ({self.timeout_seconds}s) reached. Unloading models.")
        
        # Dereference models
        self.detection_session = None
        self.processor = None
        self.recognition_model = None
        self.models_loaded = False
        
        # Force Garbage Collection to reclaim RAM/VRAM
        gc.collect() 

    def _maintenance_loop(self):
        """Background thread to check for inactivity."""
        while True:
            time.sleep(10) # Check every 10 seconds
            
            with self._lock:
                # If models are loaded AND time since last access > timeout
                if self.models_loaded and (time.time() - self.last_access_time > self.timeout_seconds):
                    self._unload_models()

    def preprocess_image(self, img: Image) -> Image:
        if not self.ENABLE_PREPROCESSING:
            return img
        try:
            processed_img = img
            original_width, original_height = processed_img.size
            if self.ENABLE_UPSCALING and original_width < self.TARGET_WIDTH:
                scale_factor = self.TARGET_WIDTH / original_width
                new_width = self.TARGET_WIDTH
                new_height = int(original_height * scale_factor)
                resample_filter = getattr(PILImage, 'Resampling', PILImage).LANCZOS
                processed_img = processed_img.resize((new_width, new_height), resample_filter)
            
            if self.ENABLE_SHARPENING:
                processed_img = processed_img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=100, threshold=5))
            return processed_img
        except Exception as e:
            print(f"[Warning] Preprocessing failed: {e}")
            return img

    def _resize(self, image: np.ndarray, w: int, h: int):
        return cv2.resize(image, (w, h), interpolation=cv2.INTER_LINEAR)

    def _detect_text_boxes(self, image: np.ndarray):
        img_height, img_width = image.shape[:2]
        resized_image = self._resize(image, self.detect_width, self.detect_height)
        
        # CPU Preprocessing
        img_normalized = resized_image.astype(np.float32) / 255.0
        img_transposed = np.transpose(img_normalized, (2, 0, 1))
        image_input_tensor = np.expand_dims(img_transposed, axis=0)
        
        size_tensor = np.array([[img_width, img_height]], dtype=np.int64)
        
        input_names = [inp.name for inp in self.detection_session.get_inputs()]
        inputs = {
            input_names[0]: image_input_tensor,
            input_names[1]: size_tensor
        }
        
        # Run Inference (CPU)
        outputs = self.detection_session.run(None, inputs)
        _, boxes, scores = outputs
        boxes, scores = boxes[0], scores[0]
        
        original_boxes = []
        for box, score in zip(boxes, scores):
            if score > self.confidence_threshold:
                x_min, y_min, x_max, y_max = box
                
                final_x_min = int(x_min)
                final_y_min = int(y_min)
                final_x_max = int(x_max)
                final_y_max = int(y_max)
                
                # Offsets
                final_x_min = max(0, final_x_min + 0)
                final_x_max = min(img_width, final_x_max + 4)
                final_y_min = max(0, final_y_min - 2)
                final_y_max = min(img_height, final_y_max + 4)
                
                if final_x_min < final_x_max and final_y_min < final_y_max:
                    original_boxes.append((final_x_min, final_y_min, final_x_max, final_y_max))
        
        return original_boxes

    async def ocr(self, img: Image) -> list[Bubble]:
        # 1. Lock ONLY the state management
        with self._lock:
            self.last_access_time = time.time()
            if not self.models_loaded:
                self._load_models()
                
        # 2. Release the lock and run the heavy math in a background thread
        return await asyncio.to_thread(self._ocr_internal, img)

    def _ocr_internal(self, img: Image) -> list[Bubble]:
        processed_img = self.preprocess_image(img)
        processed_width, processed_height = processed_img.size
        
        # Convert directly to RGB NumPy array from PIL to minimize conversion overhead
        img_array = np.array(processed_img)
        
        boxes = self._detect_text_boxes(img_array)
        if not boxes:
            return []
        
        crop_data =[] 
        
        for x1, y1, x2, y2 in boxes:
            crop = processed_img.crop((x1, y1, x2, y2))
            if crop.width >= 10 and crop.height >= 10:
                crop_data.append((crop, (x1, y1, x2, y2)))
        
        if not crop_data:
            return []

        bubbles =[]
        total_crops = len(crop_data)
        
        for i in range(0, total_crops, self.BATCH_SIZE):
            batch_chunk = crop_data[i : i + self.BATCH_SIZE]
            batch_images = [item[0] for item in batch_chunk]
            batch_boxes = [item[1] for item in batch_chunk]
            
            try:
                pixel_values = self.processor(
                    images=batch_images, 
                    return_tensors="pt", 
                    padding=True
                ).pixel_values
                
                # Lock execution to prevent VRAM crashes
                with self._gpu_semaphore:
                    generated_ids = self.recognition_model.generate(
                        pixel_values,
                        max_new_tokens=40,
                        num_beams=1,
                        use_cache=False,  # <--- Bypasses caching setup exactly as referenced
                    )
                
                batch_texts = self.processor.batch_decode(generated_ids, skip_special_tokens=True)
                
                for text, (x1, y1, x2, y2) in zip(batch_texts, batch_boxes):
                    text = re.sub(r'\s+', '', text)
                    if not text:
                        continue
                        
                    width = x2 - x1
                    height = y2 - y1
                    bubbles.append(Bubble(
                        text=text,
                        tightBoundingBox=BoundingBox(
                            x=x1 / processed_width,
                            y=y1 / processed_height,
                            width=width / processed_width,
                            height=height / processed_height,
                        ),
                        orientation=90.0 if height > width else 0.0,
                        font_size=0.04,
                        confidence=0.95, 
                    ))
                    
            except Exception as e:
                print(f"[Warning] Batch OCR failed for batch starting at {i}: {e}")
                continue
        
        return bubbles
        
# TODO: get a mac
class AppleVision(Engine):
    def __init__(self):
        print("AppleVision is not implemented yet")
        self.engine = object()

    async def ocr(self, img: Image) -> list[Bubble]:
        print("AppleVision is not implemented yet")
        return []


def initialize_engine(engine_name: str) -> Engine:
    engine_name = engine_name.strip().lower()

    if engine_name == "lens":
        return GoogleLens()
    elif engine_name == "oneocr":
        return OneOCR()
    elif engine_name == "oneocrfurigana":
        return OneOCRFurigana()
    elif engine_name == "mangaocr":
        return MangaOCR()
    elif engine_name == "mangaocrdirectml":
        return MangaOCRDirectML()
    else:
        raise ValueError(f"Invalid engine: {engine_name}")
