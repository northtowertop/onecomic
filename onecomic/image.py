import re
import os
import time
import logging
import PIL.Image
from PIL import UnidentifiedImageError

from .exceptions import ImageDownloadError
from .session import ImageSession
from .utils import ensure_file_dir_exists
from .worker import WorkerPoolMgr

HERE = os.path.abspath(os.path.dirname(__file__))
PROJECT_HOME = os.path.abspath(os.path.join(HERE, os.path.pardir))

logger = logging.getLogger(__name__)


def retry(times=3, delay=0):
    def _wrapper1(func):
        def _wrapper2(*args, **kwargs):
            i = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    i += 1
                    if i > times:
                        raise e
                    time.sleep(delay)
        return _wrapper2
    return _wrapper1


def walk(rootdir):
    for parent, dirnames, filenames in os.walk(rootdir):
        for filename in filenames:
            yield os.path.join(parent, filename)


class ImageDownloader(object):
    URL_PATTERN = re.compile(r'^https?://.*')
    SITE = ''

    def __init__(self, site):
        self.site = site

    def get_session(self):
        return ImageSession.get_session(site=self.site)

    def get_timeout(self):
        return ImageSession.get_timeout(site=self.site)

    def is_image_exists(self, image_path):
        if os.path.exists(image_path):
            try:
                self.verify_image(image_path)
                return True
            except Exception:
                pass
        return False

    @retry(times=3, delay=1)
    def download_image(self, image_url, target_path, image_pipeline=None, headers=None, **kwargs):
        if self.is_image_exists(target_path):
            return target_path
        session = self.get_session()
        headers = dict(session.headers, **(headers or {}))

        try:
            response = session.get(image_url, timeout=self.get_timeout(), headers=headers, **kwargs)
        except Exception as e:
            msg = "img download error: url=%s error: %s" % (image_url, e)
            raise ImageDownloadError(msg) from e
        if response.status_code != 200:
            msg = 'img download error: url=%s status_code=%s' % (image_url, response.status_code)
            raise ImageDownloadError(msg)

        ensure_file_dir_exists(target_path)
        with open(target_path, 'wb') as f:
            f.write(response.content)
        try:
            self.verify_image(target_path)
        except UnidentifiedImageError as e:
            os.unlink(target_path)
            raise ImageDownloadError(f'Corrupt image from {image_url}') from e

        if image_pipeline:
            image_pipeline(target_path)
        return target_path

    def verify_image(self, image_path):
        suffix = image_path.split('.')[-1]
        webp_head = bytes.fromhex("524946462A73010057454250")
        if suffix.lower() == 'webp':
            head = open(image_path, 'rb').read(12)
            if head[:4] == webp_head[:4] and head[-4:] == webp_head[-4:]:
                return True
        with PIL.Image.open(image_path) as img:
            img.verify()

    def download_images(self, image_urls, output_dir, image_pipelines=None, headers_list=None, **kwargs):
        """下载出错只打印出警告信息，不抛出异常
        """
        pool = WorkerPoolMgr.get_pool()
        future_list = []
        for idx, image_url in enumerate(image_urls, start=1):
            ext = self.find_suffix(image_url)
            target_path = os.path.join(output_dir.rstrip(), "{}.{}".format(idx, ext))
            image_pipeline = image_pipelines[idx - 1] if image_pipelines else None
            headers = headers_list[idx - 1] if headers_list else None
            future = pool.submit(
                self.download_image,
                image_url=image_url,
                target_path=target_path,
                image_pipeline=image_pipeline,
                headers=headers,
                **kwargs)
            future_list.append(future)

        # 等全部图片下载完成
        for future in future_list:
            try:
                future.result()
            except Exception as e:
                logger.warn('image download error. error=%s', e)
        return output_dir

    @staticmethod
    def find_suffix(image_url, default='jpg',
                    allow=frozenset(['jpg', 'png', 'jpeg', 'gif', 'webp'])):
        """从图片url提取图片扩展名
        :param image_url: 图片链接
        :param default: 扩展名不在 allow 内，则返回默认扩展名
        :param allow: 允许的扩展名
        :return ext: 扩展名，不包含.
        """
        url = image_url.split('?')[0]
        ext = url.rsplit('.', 1)[-1].lower()
        if ext in allow:
            return ext
        return default
