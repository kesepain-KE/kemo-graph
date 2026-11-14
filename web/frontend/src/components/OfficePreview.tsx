import FileViewer, { type ViewerOptions } from "@file-viewer/react";
import "virtual:file-viewer-renderers";
import "../styles/office-preview.css";

// The official Vite plugin registers only the installed document renderers
// and deploys their matching workers, WASM and fonts alongside the application.
const options: ViewerOptions = {
  styleIsolation: "shadow",
  theme: "light",
  locale: "zh-CN",
  fit: { mode: "width", resize: "until-interaction", padding: 24 },
  toolbar: {
    position: "top",
    download: false, // The knowledge library already downloads the original.
    exportHtml: false,
    theme: false,
    zoom: true,
    search: true,
    print: true,
  },
  docx: {
    visualPagination: true,
    renderAltChunks: false,
    externalLinkPolicy: "block",
    externalResourcePolicy: "block",
  },
  pdf: { darkMode: false },
};

export default function OfficePreview({ url, name, format }: {
  url: string; name: string; format: string;
}) {
  return <div className="office-preview" aria-label={`原文件预览 ${name}`}>
    <FileViewer url={url} filename={name} type={format} options={options} />
  </div>;
}
