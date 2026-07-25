/** Reusable auth-page UI on Radix Themes — imported via the `auth-ui` subpath so
 * headless consumers never resolve the (optional) Radix peer dependencies.
 *
 * `AuthLayout` is brand-free: the consumer passes its wordmark (typically its
 * router's `<Link to="/">`) and tagline once, usually from a thin app-side wrapper.
 */
import { EyeClosedIcon, EyeOpenIcon } from '@radix-ui/react-icons'
import { Card, Flex, Heading, IconButton, Text, TextField } from '@radix-ui/themes'
import { useId, useState } from 'react'
import type { ReactNode } from 'react'

/** Default background wash — soft radials on Radix theme color vars so light and
 * dark appearance both work. */
const DEFAULT_BACKGROUND =
  'radial-gradient(50rem 30rem at 12% -8%, var(--jade-4), transparent 62%), ' +
  'radial-gradient(46rem 28rem at 108% 108%, var(--indigo-4), transparent 58%)'

export interface AuthLayoutProps {
  /** Brand wordmark rendered as the page heading — pass your router's `<Link>`
   * for SPA navigation home. */
  wordmark: ReactNode
  tagline?: ReactNode
  /** CSS `background` override for the page wash. */
  background?: string
  cardWidth?: number
  children: ReactNode
}

/** Shared shell for public auth pages (login, register, forgot/reset password,
 * verify email): wordmark, tagline, one consistent centered card. */
export function AuthLayout({
  wordmark,
  tagline,
  background = DEFAULT_BACKGROUND,
  cardWidth = 380,
  children,
}: AuthLayoutProps) {
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="5"
      px="4"
      py="6"
      style={{ background, minHeight: '100svh' }}
    >
      <Flex direction="column" align="center" gap="1">
        <Heading size="6">{wordmark}</Heading>
        {tagline != null && (
          <Text size="2" color="gray">
            {tagline}
          </Text>
        )}
      </Flex>
      <Card size="4" style={{ width: cardWidth, maxWidth: '100%' }}>
        {children}
      </Card>
    </Flex>
  )
}

export interface PasswordFieldProps {
  label: string
  name: string
  autoComplete: 'current-password' | 'new-password'
  value: string
  onChange: (value: string) => void
  required?: boolean
  minLength?: number
  /** Muted helper line under the field; replaced by `error` when one is set. */
  hint?: string
  error?: string | null
}

/** Password input with a show/hide toggle and an optional hint/error line. */
export function PasswordField({
  label,
  name,
  autoComplete,
  value,
  onChange,
  required,
  minLength,
  hint,
  error,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)
  // Explicit htmlFor/id instead of a wrapping <label>: the toggle button and
  // hint/error line must stay out of the label subtree, or their text bleeds
  // into the field's accessible name.
  const id = useId()
  return (
    <Flex direction="column" gap="1">
      <Text as="label" htmlFor={id} weight="medium" size="2">
        {label}
      </Text>
      <TextField.Root
        id={id}
        type={visible ? 'text' : 'password'}
        name={name}
        autoComplete={autoComplete}
        required={required}
        minLength={minLength}
        value={value}
        color={error ? 'red' : undefined}
        onChange={(e) => onChange(e.target.value)}
      >
        <TextField.Slot side="right">
          <IconButton
            type="button"
            variant="ghost"
            color="gray"
            size="1"
            aria-label={visible ? 'Hide password' : 'Show password'}
            onClick={() => setVisible((v) => !v)}
          >
            {visible ? <EyeOpenIcon /> : <EyeClosedIcon />}
          </IconButton>
        </TextField.Slot>
      </TextField.Root>
      {(error ?? hint) && (
        <Text size="1" color={error ? 'red' : 'gray'}>
          {error ?? hint}
        </Text>
      )}
    </Flex>
  )
}
