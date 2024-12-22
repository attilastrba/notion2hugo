from dataclasses import dataclass

from notion2hugo.base import (
    BaseFormatter,
    BaseFormatterConfig,
    Blob,
    BlobType,
    ContentWithAnnotation,
    PageContent,
    register_handler,
)
from collections import OrderedDict


@dataclass(frozen=True)
class HugoFormatterConfig(BaseFormatterConfig):
    pass


@register_handler(HugoFormatterConfig)
class HugoFormatter(BaseFormatter):
    def __init__(self, config: HugoFormatterConfig):
        super(HugoFormatter, self).__init__(config)
        self.config: HugoFormatterConfig = config

    async def async_process(self, content: PageContent) -> PageContent:
        header_props = []

        # Sort header and filter out stuff
        content.properties.pop("# Status")
        key_order = ['Title', 'featureImage', 'Date', 'Tags', 'Summary', 'Categories', 'Lesedauer']
        sorted_data = OrderedDict((key, content.properties[key]) for key in key_order if key in content.properties)
        # Check if keys exist and construct the OrderedDict

        content.properties=sorted_data

        header_props.extend(
            f"{k}: {v}" for k, v in content.properties.items() if v
        )

        header_blob = Blob(
            id="header",
            rich_text=[
                ContentWithAnnotation(plain_text="---\n"),
                ContentWithAnnotation(plain_text="\n".join(header_props)),
                ContentWithAnnotation(plain_text="\n---\n"),
            ],
            type=BlobType.PARAGRAPH,
            children=None,
            file=None,
            language=None,
            table_width=None,
            table_cells=None,
            is_checked=None,
            url=None
        )

        return PageContent(
            blobs=content.blobs,
            id=content.id,
            properties=content.properties,
            footer=None,
            header=header_blob,
        )
