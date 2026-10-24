/** Browser-only adapter. Production externalizes @decky/ui to Decky's native controls. */
import { cloneElement, ReactElement, ReactNode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

export const staticClasses = { Title: "preview-title" };
/** The values of @decky/ui's GamepadButton that the editor uses. */
export enum GamepadButton { OK = 1, CANCEL = 2, SECONDARY = 3, OPTIONS = 4,
  TRIGGER_LEFT = 7, TRIGGER_RIGHT = 8, DIR_UP = 9, DIR_DOWN = 10, DIR_LEFT = 11, DIR_RIGHT = 12 }
export type GamepadEvent = CustomEvent<{ button: number }>;
/** Steam's router; tests set the running game here. */
export const Router: { MainRunningApp?: { appid: number; display_name: string } } = {};
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
/** Like Steam's toggle: it flips itself and follows only a changed checked prop. */
export function ToggleField({ label, description, checked, onChange, disabled }: any) {
  const [shown, setShown] = useState(checked);
  useEffect(() => setShown(checked), [checked]);
  return <label className="preview-toggle"><div>{label}{description && <small>{description}</small>}</div>
    <input role="switch" aria-label={label} type="checkbox" checked={shown} disabled={disabled}
      onChange={event => { setShown(event.target.checked); onChange(event.target.checked); }} /></label>;
}
/** Like Steam's dropdown: it shows the pick at once and follows only a changed selectedOption (or a remount). */
export function DropdownItem({ label, description, selectedOption, rgOptions, onChange, disabled, strDefaultLabel }: any) {
  const [shown, setShown] = useState(selectedOption);
  useEffect(() => setShown(selectedOption), [selectedOption]);
  return <label className="preview-dropdown">{label}{description && <small>{description}</small>}<select aria-label={label} value={shown} disabled={disabled}
    onChange={event => {
      const option = rgOptions.find((o: any) => String(o.data) === event.target.value);
      if (option) { setShown(option.data); onChange(option); }
    }}>
    {!rgOptions.some((o: any) => o.data === shown) && <option value="">{strDefaultLabel || "Choose…"}</option>}
    {rgOptions.map((o: any) => <option key={String(o.data)} value={o.data}>{o.label}</option>)}
  </select></label>;
}
export const TextField = ({ label, value, onChange, disabled, focusOnMount }: any) => <label className="preview-text">{label}<input aria-label={label} value={value} onChange={onChange} disabled={disabled} autoFocus={focusOnMount} /></label>;
export const ButtonItem = ({ children, onClick, disabled }: any) => <button className="preview-button" onClick={onClick} disabled={disabled}>{children}</button>;
// Keys stand in for the gamepad: Enter A, Escape B, arrows the D-pad, z/c LT/RT, x X, y Y.
const pad: Record<string, GamepadButton> = { Enter: GamepadButton.OK, Escape: GamepadButton.CANCEL, ArrowUp: GamepadButton.DIR_UP,
  ArrowDown: GamepadButton.DIR_DOWN, ArrowLeft: GamepadButton.DIR_LEFT, ArrowRight: GamepadButton.DIR_RIGHT,
  z: GamepadButton.TRIGGER_LEFT, c: GamepadButton.TRIGGER_RIGHT, x: GamepadButton.SECONDARY, y: GamepadButton.OPTIONS };
/** Like Steam's: a click (a tap) activates; buttons arrive as gamepad events. `data-legend` holds Steam's button hints. */
export function Focusable({ children, className, style, onCancelButton, onOKButton, onActivate, onButtonDown,
  onGamepadDirection, onGamepadBlur, actionDescriptionMap }: any) {
  return <div className={className} style={style} tabIndex={onOKButton || onActivate ? 0 : undefined} onClick={onActivate}
    data-legend={actionDescriptionMap && JSON.stringify(actionDescriptionMap)}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) onGamepadBlur?.(event); }}
    onKeyDown={event => {
      const button = pad[event.key];
      if (button === undefined) return;
      const evt = { detail: { button }, preventDefault: () => event.preventDefault(), stopPropagation: () => event.stopPropagation() };
      if (button === GamepadButton.CANCEL) return onCancelButton?.(evt);
      onButtonDown?.(evt);  // Like Steam: every button, then the matching handler.
      if (button === GamepadButton.OK) (onOKButton ?? onActivate)?.(evt);
      else if (button >= GamepadButton.DIR_UP && button <= GamepadButton.DIR_RIGHT && onGamepadDirection) {
        onGamepadDirection(evt);
        event.preventDefault();  // Like Steam on the Legion: an element that handles directions keeps the focus.
      }
    }}>{children}</div>;
}
export const DialogButton = ({ children, onClick, disabled, style }: any) => <button className="preview-button" style={style} onClick={onClick} disabled={disabled}>{children}</button>;
export const DialogHeader = ({ children }: any) => <h2>{children}</h2>;
export const DialogBody = ({ children, style }: any) => <div style={style}>{children}</div>;
export const DialogFooter = ({ children, style }: any) => <div style={style}>{children}</div>;
export const ModalRoot = ({ children, className }: any) => <div className={`preview-fullmodal ${className ?? ""}`} role="dialog">{children}</div>;
/** Like Steam's: B (Escape here) closes it as Cancel does. */
export const ConfirmModal = ({ strTitle, strDescription, strOKButtonText, strCancelButtonText, strMiddleButtonText,
  onOK, onCancel, onMiddleButton, closeModal, bOKDisabled, children }: any) =>
  <div className="preview-modal" role="dialog" aria-label={strTitle}
    onKeyDown={event => { if (event.key === "Escape") { onCancel?.(); closeModal?.(); } }}><h2>{strTitle}</h2>{strDescription && <p>{strDescription}</p>}{children}
    <button disabled={bOKDisabled} onClick={() => { onOK?.(); closeModal?.(); }}>{strOKButtonText}</button>
    {strMiddleButtonText && <button onClick={() => { onMiddleButton?.(); closeModal?.(); }}>{strMiddleButtonText}</button>}
    <button onClick={() => { onCancel?.(); closeModal?.(); }}>{strCancelButtonText}</button></div>;
export function showModal(element: ReactNode, _parent?: unknown, props: { fnOnClose?(): void } = {}) {
  const host = document.createElement("div"); host.className = "preview-modal-host"; document.body.append(host);
  const root = createRoot(host);
  let open = true;
  const close = () => { if (open) { open = false; root.unmount(); host.remove(); props.fnOnClose?.(); } };
  root.render(cloneElement(element as ReactElement<any>, { closeModal: close }));
  return { Close: close, Update: (next: ReactNode) => root.render(next) };
}
