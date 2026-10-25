"""
Certificate Authorities: one per organisation, like Fabric CA.

Two-step issuance, exactly as in Fabric:

  register  the CA's registrar (an admin) creates an identity record:
            name, one-time secret, type (client/peer/admin/orderer), attributes
  enroll    whoever knows the secret sends a Certificate Signing Request (CSR).
            The CSR contains only the PUBLIC key; the private key is generated
            on the requester's machine and never sent anywhere.

The issued certificate carries:
  OU=<type>                    -> how the MSP classifies the identity
  extension 1.2.3.4.5.6.7.8.1  -> {"attrs": {"role": "clerk", "dept": ...}}
"""
