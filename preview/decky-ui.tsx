/** Browser-only adapter. Production externalizes @decky/ui to Decky's native controls. */
import { cloneElement, ReactElement, ReactNode } from "react";
import { createRoot } from "react-dom/client";

export const staticClasses = { Title: "preview-title" };
export const PanelSection = ({ title, children }: { title?: string; children?: ReactNode }) =>
  <section className="preview-section">{title && <h2>{title}</h2>}{children}</section>;
export const PanelSectionRow = ({ children }: { children?: ReactNode }) => <div className="preview-row">{children}</div>;
export const Field = ({ ref, label, description, children, icon, onActivate }: any) =>
  <div ref={ref} className="preview-field" tabIndex={onActivate ? 0 : undefined} role={onActivate ? "button" : undefined} onClick={onActivate}>
    <div>{icon && <span className="preview-icon">{icon}</span>}{label}{children && <span className="preview-right">{children}</span>}</div>{description && <small>{description}</small>}</div>;
export const SliderField = ({ label, description, value, min, max, step, onChange, disabled, showValue, valueSuffix }: any) =>
  <label className="preview-slider"><div>{label}{showValue && <span className="preview-right">{Number(value).toFixed(1)}{valueSuffix}</span>}</div>
    {description && <small>{description}</small>}<input aria-label={label} type="range" min={min} max={max} step={step} value={value} disabled={disabled}
      onChange={event => onChange?.(Number(event.target.value))} /></label>;
export const ToggleField = ({ label, description, checked, onChange, disabled }: any) =>
  <label className="preview-toggle"><div>{label}{description && <small>{description}</small>}</div>
    <input role="switch" aria-label={label} type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} /></label>;
export const DropdownItem = ({ label, description, selectedOption, rgOptions, onChange, disabled, strDefaultLabel }: any) =>
  <label className="preview-dropdown">{label}{description && <small>{description}</small>}<select aria-label={label} value={selectedOption} disabled={disabled}
    onChange={event => onChange(rgOptions.find((o: any) => String(o.data) === event.target.value))}>
    {!rgOptions.some((o: any) => o.data === selectedOption) && <option value="">{strDefaultLabel || "Choose…"}</option>}
    {rgOptions.map((o: any) => <option key={String(o.data)} value={o.data}>{o.label}</option>)}
  </select></label>;
export const TextField = ({ label, value, onChange, disabled, focusOnMount }: any) => <label className="preview-text">{label}<input aria-label={label} value={value} onChange={onChange} disabled={disabled} autoFocus={focusOnMount} /></label>;
export const ButtonItem = ({ children, onClick, disabled }: any) => <button className="preview-button" onClick={onClick} disabled={disabled}>{children}</button>;
// Escape stands in for the gamepad B button.
export const Focusable = ({ children, className, onCancelButton }: any) =>
  <div className={className} onKeyDown={event => { if (event.key === "Escape") onCancelButton?.(event); }}>{children}</div>;
export const ConfirmModal = ({ strTitle, strDescription, strOKButtonText, strCancelButtonText, strMiddleButtonText,
  onOK, onMiddleButton, closeModal, bOKDisabled, children }: any) =>
  <div className="preview-modal" role="dialog" aria-label={strTitle}><h2>{strTitle}</h2>{strDescription && <p>{strDescription}</p>}{children}
    <button disabled={bOKDisabled} onClick={() => { onOK?.(); closeModal?.(); }}>{strOKButtonText}</button>
    {strMiddleButtonText && <button onClick={() => { onMiddleButton?.(); closeModal?.(); }}>{strMiddleButtonText}</button>}
    <button onClick={closeModal}>{strCancelButtonText}</button></div>;
export function showModal(element: ReactNode) {
  const host = document.createElement("div"); host.className = "preview-modal-host"; document.body.append(host);
  const root = createRoot(host);
  const close = () => { root.unmount(); host.remove(); };
  root.render(cloneElement(element as ReactElement<any>, { closeModal: close }));
  return { Close: close, Update: (next: ReactNode) => root.render(next) };
}
