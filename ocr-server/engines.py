from abc import ABC, abstractmethod
from math import pi
from typing import TypedDict


import chrome_lens_py
from chrome_lens_py.utils.lens_betterproto import LensOverlayObjectsResponse
from PIL.Image import Image
import cv2
import numpy as np
import onnxruntime as ort

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

class MeikiMangaOCR(Engine):
    """
    OCR engine that uses Meiki text detection to find text boxes,
    then runs Manga OCR on each detected region.
    """
    
    def __init__(
        self, 
        model_path: str = "meiki.text.detect.small.v0.onnx",
        confidence_threshold: float = 0.5,
        pretrained_model_name_or_path: str = 'kha-white/manga-ocr-base',
        force_cpu: bool = False
    ):
        """
        Initialize the MeikiMangaOCR engine.
        
        Args:
            model_path: Path to the meiki ONNX model
            confidence_threshold: Minimum confidence for text detection (0.0-1.0)
            pretrained_model_name_or_path: Manga OCR model name or path
            force_cpu: Force manga-ocr to use CPU
        """
        # Initialize text detection model
        try:
            self.detection_session = ort.InferenceSession(
                model_path, 
                providers=['CPUExecutionProvider']
            )
            self.model_size = 640
            self.confidence_threshold = confidence_threshold
            print(f"[MeikiMangaOCR] Loaded text detection model: {model_path}")
        except Exception as e:
            print(f"[Error] Failed to load Meiki text detection model: {e}")
            raise
        
        # Initialize manga-ocr
        try:
            from manga_ocr import MangaOcr as MOCR
            import re
            import logging
            from loguru import logger
            
            # Disable verbose logging
            logger.disable('manga_ocr')
            logging.getLogger('transformers').setLevel(logging.ERROR)
            
            # Override post-processing to remove spaces
            from manga_ocr import ocr
            def empty_post_process(text):
                text = re.sub(r'\s+', '', text)
                return text
            ocr.post_process = empty_post_process
            
            self.manga_ocr = MOCR(pretrained_model_name_or_path, force_cpu)
            print(f"[MeikiMangaOCR] Loaded Manga OCR model: {pretrained_model_name_or_path}")
        except ImportError as e:
            print(f"[Error] manga-ocr not installed: {e}")
            raise
        except Exception as e:
            print(f"[Error] Failed to initialize Manga OCR: {e}")
            raise

    def _resize_and_pad(self, image: np.ndarray, size: int):
        """
        Resize and pad image to model input size, maintaining aspect ratio.
        
        Returns:
            - Padded image
            - Resize ratio
            - Padding width
            - Padding height
        """
        original_height, original_width, _ = image.shape
        
        ratio = min(size / original_width, size / original_height)
        new_width = int(original_width * ratio)
        new_height = int(original_height * ratio)
        
        resized_image = cv2.resize(
            image, 
            (new_width, new_height), 
            interpolation=cv2.INTER_LINEAR
        )
        
        padded_image = np.zeros((size, size, 3), dtype=np.uint8)
        pad_w = (size - new_width) // 2
        pad_h = (size - new_height) // 2
        padded_image[pad_h:pad_h + new_height, pad_w:pad_w + new_width] = resized_image
        
        return padded_image, ratio, pad_w, pad_h

    def _detect_text_boxes(self, image: np.ndarray):
        """
        Run text detection on the image.
        
        Returns:
            List of bounding boxes in original image coordinates: [(x1, y1, x2, y2), ...]
        """
        # Prepare image for model
        padded_image, ratio, pad_w, pad_h = self._resize_and_pad(image, self.model_size)
        
        # Normalize and transpose
        img_normalized = padded_image.astype(np.float32) / 255.0
        img_transposed = np.transpose(img_normalized, (2, 0, 1))
        image_input_tensor = np.expand_dims(img_transposed, axis=0)
        
        # Prepare size input
        sizes_input_tensor = np.array([[self.model_size, self.model_size]], dtype=np.int64)
        
        # Run inference
        input_names = [inp.name for inp in self.detection_session.get_inputs()]
        inputs = {
            input_names[0]: image_input_tensor,
            input_names[1]: sizes_input_tensor
        }
        
        outputs = self.detection_session.run(None, inputs)
        labels, boxes, scores = outputs
        
        # Post-process: convert to original image coordinates
        boxes = boxes[0]
        scores = scores[0]
        
        original_boxes = []
        for box, score in zip(boxes, scores):
            if score > self.confidence_threshold:
                x_min, y_min, x_max, y_max = box
                
                # Remove padding
                x_min_unpadded = x_min - pad_w
                y_min_unpadded = y_min - pad_h
                x_max_unpadded = x_max - pad_w
                y_max_unpadded = y_max - pad_h
                
                # Scale back to original size
                final_x_min = int(x_min_unpadded / ratio)
                final_y_min = int(y_min_unpadded / ratio)
                final_x_max = int(x_max_unpadded / ratio)
                final_y_max = int(y_max_unpadded / ratio)
                
                # Clamp to image bounds
                final_x_min = max(0, final_x_min)
                final_y_min = max(0, final_y_min)
                final_x_max = min(image.shape[1], final_x_max)
                final_y_max = min(image.shape[0], final_y_max)
                
                original_boxes.append((final_x_min, final_y_min, final_x_max, final_y_max))
        
        return original_boxes

    async def ocr(self, img: Image) -> list[Bubble]:
        """
        Run OCR on the image: detect text boxes, then run manga-ocr on each.
        
        Args:
            img: PIL Image to process
            
        Returns:
            List of Bubble objects with detected text and bounding boxes
        """
        # Convert PIL to numpy array (BGR for OpenCV compatibility)
        img_array = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        original_height, original_width = img_array.shape[:2]
        
        # Detect text boxes
        boxes = self._detect_text_boxes(img_array)
        
        if not boxes:
            print("[MeikiMangaOCR] No text boxes detected")
            return []
        
        print(f"[MeikiMangaOCR] Detected {len(boxes)} text boxes")
        
        # Process each detected box
        bubbles = []
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            try:
                # Crop the text region from original PIL image
                crop = img.crop((x1, y1, x2, y2))
                
                # Skip if crop is too small
                if crop.width < 10 or crop.height < 10:
                    continue
                
                # Run manga-ocr on the cropped region
                text = self.manga_ocr(crop)
                
                if not text or not text.strip():
                    continue
                
                # Calculate normalized bounding box
                width = x2 - x1
                height = y2 - y1
                
                # Determine orientation based on aspect ratio
                # Vertical text typically has height > width
                orientation = 90.0 if height > width else 0.0
                
                bubble = Bubble(
                    text=text.strip(),
                    tightBoundingBox=BoundingBox(
                        x=x1 / original_width,
                        y=y1 / original_height,
                        width=width / original_width,
                        height=height / original_height,
                    ),
                    orientation=orientation,
                    font_size=0.04,
                    confidence=0.95,  # Manga OCR typically has high confidence
                )
                bubbles.append(bubble)
                
            except Exception as e:
                print(f"[Warning] Failed to process box {i}: {e}")
                continue
        
        print(f"[MeikiMangaOCR] Successfully processed {len(bubbles)} text regions")
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
    elif engine_name == "meikimanga":
        return MeikiMangaOCR()
    else:
        raise ValueError(f"Invalid engine: {engine_name}")
