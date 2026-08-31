import * as Select from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'

const EMPTY = '__all__'

export function SelectField({ value = '', onValueChange, options, className = '', ariaLabel }) {
  return <Select.Root value={value || EMPTY} onValueChange={next => onValueChange(next === EMPTY ? '' : next)}>
    <Select.Trigger className={`select-trigger ${className}`} aria-label={ariaLabel}>
      <Select.Value /> <Select.Icon><ChevronDown size={15} /></Select.Icon>
    </Select.Trigger>
    <Select.Portal><Select.Content className="select-content" position="popper" sideOffset={6}>
      <Select.Viewport>{options.map(option => <Select.Item className="select-item" key={option.value || EMPTY} value={option.value || EMPTY}>
        <Select.ItemText>{option.label}</Select.ItemText><Select.ItemIndicator><Check size={14} /></Select.ItemIndicator>
      </Select.Item>)}</Select.Viewport>
    </Select.Content></Select.Portal>
  </Select.Root>
}
