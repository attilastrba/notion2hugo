#!/usr/bin/env python3

"""Defines the top level abstraction which encapsulates export logic."""
import asyncio
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pprint import pformat
from typing import Any, AsyncIterator, Dict, List, Optional
from notion2hugo.utils import get_logger
import re
import shutil

import requests
from notion_client import AsyncClient
from notion_client.helpers import async_iterate_paginated_api

from notion2hugo import NOTION_DATABASE_ID, NOTION_TOKEN
from notion2hugo.base import (
    BaseProvider,
    BaseProviderConfig,
    Blob,
    BlobType,
    ContentWithAnnotation,
    PageContent,
    Properties,
    register_handler,
)


@dataclass(frozen=True)
class NotionPageMetadata:
    archived: bool
    id: str
    last_edited_time: str
    parent: Dict[str, str]
    properties: Dict[str, Any]
    url: str

    @classmethod
    def init(cls, **kwargs) -> "NotionPageMetadata":
        vals = {}
        for var in fields(cls):
            assert var.name in kwargs, f"Missing expected field {var.name}"
            vals[var.name] = kwargs[var.name]
        return NotionPageMetadata(**vals)


@dataclass(frozen=True)
class NotionBlockData:
    id: str
    content: Dict[str, Any]
    type: BlobType
    children: Optional[List["NotionBlockData"]]


class NotionParser:
    def __init__(self, download_dir, tmp_cache_dir: str):
        self.tmp_cache_dir = tmp_cache_dir
        self.download_dir = download_dir
        self.img_count = 0
        self.logger = get_logger("NotionParser")


    def parse_block(self, block: NotionBlockData) -> Blob:
        table_cells = None
        # used for the video
        url = ""
        img_path = None
        rich_text = []

        if block.content.get("rich_text"):
            # majority of elems
            rich_text = [
                ContentWithAnnotation(
                    plain_text=t.get("plain_text"),
                    href=t.get("href"),
                    is_equation=t.get("type") == "equation",
                    is_toggleable=block.content.get("is_toggleable", False),
                    **t.get("annotations", {}),
                    is_feature_image=t.get("plain_text") == "featureimage",
                )
                for t in block.content["rich_text"]
            ]
        elif block.content.get("caption") or block.content.get("file"):
            # image
            rich_text = [
                ContentWithAnnotation(
                    is_caption=True,
                    plain_text=t.get("plain_text"),
                    href=t.get("href"),
                    **t.get("annotations", {}),
                    is_feature_image=t.get("plain_text") == "featureimage",
                )
                for t in block.content.get("caption", [])
            ]
            if block.type == 'image':
                # download and cache the img file locally
                remote_url = block.content.get("file", {}).get("url", None)
                assert remote_url, f"File url expected for image {block}"
                img_path = self.download_image_locally(remote_url)
            if block.type == 'video':
                url =  block.content.get('external')['url']
        elif block.content.get("expression"):
            # equation
            rich_text = [ContentWithAnnotation(plain_text=block.content["expression"])]
        elif block.content.get("cells"):
            # table_row
            table_cells = [
                [
                    ContentWithAnnotation(
                        plain_text=t.get("plain_text"),
                        href=t.get("href"),
                        **t.get("annotations", {}),
                        is_feature_image=t.get("plain_text") == "featureimage",
                    )
                    for t in cell
                ]
                for cell in block.content["cells"]
            ]

        return Blob(
            id=block.id,
            rich_text=rich_text,
            type=block.type,
            children=[self.parse_block(c) for c in block.children]
            if block.children
            else None,
            file=img_path,
            language=block.content.get("language", None),  # code block
            table_width=block.content.get("table_width"),  # table
            table_cells=table_cells,
            is_checked=block.content.get("checked", None),  # todo
            url = url,
        )

    def download_image_locally(self, url: str) -> str:
        with requests.get(url, stream=True) as response:
            content_type = response.headers["Content-Type"].split("/")
            assert (
                len(content_type) == 2 and content_type[0] == "image"
            ), f"URL expected to contain image, found {content_type}"
            self.img_count = self.img_count+1

            img_path = os.path.join(
                self.download_dir, f"img_{self.img_count}.{content_type[1]}"
            )
            if os.path.exists(img_path):
                self.logger.info(f"Skipping download image {img_path}")
                return img_path
            self.logger.info(f"Downloading image {img_path}")
            # from IPython import embed
            # import nest_asyncio
            # nest_asyncio.apply()
            # embed(using='asyncio')
            with open(img_path, "wb") as fp:
                local_path = fp.name
                for chunk in response.iter_content(chunk_size=10 * 1024):
                    fp.write(chunk)
        return local_path

    def parse_properties(self, metadata: Dict[str, Any]) -> Properties:
        prop: Properties = {}
        #get summary
        for k, v in metadata.items():
            if v["type"] in ("relation"):
                # relation
                continue
            elif v["type"] == "title":
                # title
                values = ["'" + c["plain_text"] + "'" for c in v["title"]]
                prop["Title"] = values[0] if len(values) == 1 else values
                continue
            c = v.get(v["type"], {})
            if not c or isinstance(c, (str, int, bool)):
                # last_edited_time, created_time
                prop[k] = c
            elif isinstance(c, (list, tuple)):
                # multi_select
                prop[k] = list(
                    filter(lambda x: x is not None, [t.get("name") for t in c])
                )
            elif isinstance(c, dict):
                if c.get("name"):
                    # select, status,
                    value = c["name"]
                elif c.get("type"):
                    # formula
                    value = c[c["type"]]
                elif c.get("prefix") and c.get("number"):
                    # unique_id
                    value = f"{c['prefix']}_{c['number']}"
                elif c.get("start"):
                    # date
                    value = c["start"]
                prop[k] = value
            else:
                raise NotImplementedError(f"{k}, {v} type not handled")
        #add my custom modification
        #get summary
        if 'Summary' in metadata:
            rich_text = metadata['Summary'].get('rich_text', [])
            if rich_text and isinstance(rich_text, list):
                content = rich_text[0].get('text', {}).get('content', None)
                if content:
                    prop['Summary'] = f"'{content}'"

        # add ReadingTime
        prop["lesedauer"] = "ReadingTime"
        return prop


@dataclass(frozen=True)
class NotionProviderConfig(BaseProviderConfig):
    database_id: str = field(default=NOTION_DATABASE_ID)
    filter: Dict[str, Any] = field(default_factory=dict)
    tmp_cache_dir: str = field(init=False)
    download_dir: str = field(init=False)

    def __post_init__(
        self,
    ):
        assert self.database_id, f"database_id={self.database_id} not valid."
        tmp_cache_dir = tempfile.mkdtemp(prefix="images_", dir=f"/tmp/{__package__}")
        object.__setattr__(self, "tmp_cache_dir", tmp_cache_dir)

        # download_dir = os.path.join(
        #     self.config.parent_dir, post_dir_name, f"{post_dir_name}_{self.POST_IMAGES_DIR}"
        # )
        download_dir = "download"
        os.makedirs(download_dir, exist_ok=True)
        object.__setattr__(self, "download_dir", download_dir)


@register_handler(NotionProviderConfig)
class NotionProvider(BaseProvider):
    def __init__(self, config: NotionProviderConfig):
        super(NotionProvider, self).__init__(config)
        self.config: NotionProviderConfig = config
        self.client = AsyncClient(auth=NOTION_TOKEN)

    async def async_fetch_pages_from_db(self) -> List[NotionPageMetadata]:
        # fetch all available pages (metadata) from db
        page_metadatas: List[NotionPageMetadata] = []
        async for responses in async_iterate_paginated_api(
            self.client.databases.query, **asdict(self.config)
        ):
            for resp in responses:
                assert isinstance(resp, dict), resp
                if resp.get("object") == "page":
                    page_metadatas.append(NotionPageMetadata.init(**resp))
        return page_metadatas

    async def async_fetch_block_content(self, block_id: str) -> List[NotionBlockData]:
        block_data: List[NotionBlockData] = []
        async for blocks in async_iterate_paginated_api(
            self.client.blocks.children.list,
            block_id=block_id,
            page_size=100,
        ):
            for block in blocks:
                if block["has_children"]:
                    # fetch block content recursively
                    children_block_data = await self.async_fetch_block_content(
                        block["id"]
                    )
                else:
                    children_block_data = None
                assert not block["has_children"] or children_block_data is not None
                self.logger.debug(pformat(block))
                block_data.append(
                    NotionBlockData(
                        id=block["id"],
                        content=block[block["type"]],
                        type=BlobType(block["type"]),
                        children=children_block_data,
                    )
                )
        return block_data

    def sanitize_path(self, name: str) -> str:
        pattern = re.compile(r"[^a-zA-Z0-9-_\.]")
        name = name.replace(" ", "_").lower()
        return pattern.sub("", name)

    async def async_fetch_and_parse_page_content(
        self, metadata: NotionPageMetadata
    ) -> PageContent:
        parser = NotionParser(self.config.download_dir, self.config.tmp_cache_dir)
        # fetch and parse page content
        block_data = await self.async_fetch_block_content(metadata.id)
        blobs = list(map(parser.parse_block, block_data))
        properties = parser.parse_properties(metadata.properties)

        feature_image_file = ""
        for blob in blobs:
            for annotation in blob.rich_text:
                if annotation.plain_text == "featureimage" and annotation.is_feature_image:
                    feature_image_file = blob.file
                    break  # Stop further iteration if the image is found

        #Copy the thumbnail to the assets
        #TODO Battika make this constants somwhere nice
        extension = os.path.splitext(feature_image_file)[1]
        feature_image_dest = f"images/thumb_{self.sanitize_path(properties['Title'])}{extension}"
        shutil.copy(feature_image_file, f"../assets/{feature_image_dest}")
        properties['featureImage'] = feature_image_dest
        # .post_images_dir

        return PageContent(id=metadata.id, blobs=blobs, properties=properties)

    def cleanup(self):
        if os.path.exists(self.config.tmp_cache_dir):
            shutil.rmtree(self.config.tmp_cache_dir)
        self.logger.info(
            f"Cleaned up tmp dir for caching images = {self.config.tmp_cache_dir}"
        )

    async def async_iterate(self) -> AsyncIterator[PageContent]:
        self.logger.info("Querying Notion db")
        page_metadatas = await self.async_fetch_pages_from_db()
        self.logger.info(f"Notion db returned {len(page_metadatas)} pages.")

        for page in asyncio.as_completed(
            [
                self.async_fetch_and_parse_page_content(metadata)
                for metadata in page_metadatas
            ]
        ):
            yield await page

        self.logger.info("Completed retrieving all pages from db.")
        self.cleanup()
