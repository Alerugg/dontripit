import AuthShell from '../../components/auth/AuthShell'

export const metadata = {
  title: 'Crear cuenta',
  description: 'Crea tu cuenta de Don’tRipIt para organizar colección y wishlist por impresión física.',
}

export default async function RegisterPage({ searchParams }) {
  const params = await searchParams
  return <AuthShell mode="register" next={params?.next || '/dashboard'} />
}
