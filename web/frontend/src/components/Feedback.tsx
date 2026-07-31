import { AlertCircle, LoaderCircle } from "lucide-react";

export function LoadingState({ label = "正在加载…" }: { label?: string }) {
  return (
    <div className="feedback-state">
      <LoaderCircle className="spin" size={22} />
      <span>{label}</span>
    </div>
  );
}

export function ErrorNotice({ message }: { message: string }) {
  return (
    <div className="notice notice--error" role="alert">
      <AlertCircle size={17} />
      <span>{message}</span>
    </div>
  );
}

export function InfoNotice({ message }: { message: string }) {
  return (
    <div className="notice notice--info">
      <AlertCircle size={17} />
      <span>{message}</span>
    </div>
  );
}
